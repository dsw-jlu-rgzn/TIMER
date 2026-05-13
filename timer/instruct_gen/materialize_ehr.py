import argparse
import csv
import json
from collections import defaultdict
from datetime import date, datetime
from html import escape
from pathlib import Path

from google.cloud import bigquery


DOMAIN_CONFIGS = [
    {
        "table": "visit_occurrence",
        "domain": "visit",
        "date_col": "visit_start_date",
        "concept_id_col": "visit_concept_id",
        "visit_col": "visit_occurrence_id",
        "extra_cols": ["visit_end_date", "visit_source_value"],
    },
    {
        "table": "condition_occurrence",
        "domain": "condition",
        "date_col": "condition_start_date",
        "concept_id_col": "condition_concept_id",
        "visit_col": "visit_occurrence_id",
        "extra_cols": ["condition_end_date", "condition_source_value"],
    },
    {
        "table": "drug_exposure",
        "domain": "drug",
        "date_col": "drug_exposure_start_date",
        "concept_id_col": "drug_concept_id",
        "visit_col": "visit_occurrence_id",
        "extra_cols": ["drug_exposure_end_date", "drug_source_value"],
    },
    {
        "table": "procedure_occurrence",
        "domain": "procedure",
        "date_col": "procedure_date",
        "concept_id_col": "procedure_concept_id",
        "visit_col": "visit_occurrence_id",
        "extra_cols": ["procedure_source_value"],
    },
    {
        "table": "measurement",
        "domain": "measurement",
        "date_col": "measurement_date",
        "concept_id_col": "measurement_concept_id",
        "visit_col": "visit_occurrence_id",
        "extra_cols": ["value_as_number", "unit_source_value", "measurement_source_value"],
    },
    {
        "table": "observation",
        "domain": "observation",
        "date_col": "observation_date",
        "concept_id_col": "observation_concept_id",
        "visit_col": "visit_occurrence_id",
        "extra_cols": ["value_as_string", "observation_source_value"],
    },
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Materialize OMOP BigQuery records into TIMER EHR JSONL."
    )
    parser.add_argument("--project_id", required=True, help="Google Cloud project ID")
    parser.add_argument("--dataset_id", required=True, help="BigQuery dataset ID containing OMOP tables")
    parser.add_argument("--sampled_patients_csv", required=True, help="CSV containing a person_id column")
    parser.add_argument("--output_jsonl", required=True, help="Path to write materialized EHR JSONL")
    parser.add_argument("--batch_size", type=int, default=500, help="Number of person IDs per BigQuery batch")
    parser.add_argument(
        "--skip_missing_tables",
        action="store_true",
        help="Skip unavailable OMOP tables instead of failing.",
    )
    return parser.parse_args()


def read_person_ids(csv_path):
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        if "person_id" not in reader.fieldnames:
            raise ValueError(f"{csv_path} must contain a person_id column")
        return [int(row["person_id"]) for row in reader if row.get("person_id")]


def chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def normalize_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def query_domain(client, args, config, person_ids):
    extra_select = ",\n            ".join(
        f"t.{col}" for col in config["extra_cols"]
    )
    if extra_select:
        extra_select = ",\n            " + extra_select

    query = f"""
        SELECT
            t.person_id,
            t.{config["visit_col"]} AS visit_occurrence_id,
            t.{config["date_col"]} AS event_date,
            t.{config["concept_id_col"]} AS concept_id,
            c.concept_name AS concept_name
            {extra_select}
        FROM `{args.project_id}.{args.dataset_id}.{config["table"]}` AS t
        LEFT JOIN `{args.project_id}.{args.dataset_id}.concept` AS c
            ON t.{config["concept_id_col"]} = c.concept_id
        WHERE t.person_id IN UNNEST(@person_ids)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("person_ids", "INT64", person_ids)
        ]
    )
    try:
        return list(client.query(query, job_config=job_config).result())
    except Exception:
        if args.skip_missing_tables:
            print(f"Skipping unavailable or incompatible table: {config['table']}")
            return []
        raise


def event_to_xml(domain, row):
    row_data = dict(row.items())
    attrs = {
        "type": domain,
        "date": normalize_value(row_data.pop("event_date", "")),
        "concept_id": row_data.pop("concept_id", ""),
        "concept_name": row_data.pop("concept_name", ""),
    }
    attrs = {k: v for k, v in attrs.items() if v not in (None, "")}
    attr_text = " ".join(f'{k}="{escape(str(v))}"' for k, v in attrs.items())

    details = []
    for key, value in row_data.items():
        if key in {"person_id", "visit_occurrence_id"} or value in (None, ""):
            continue
        details.append(f"<field name=\"{escape(key)}\">{escape(str(normalize_value(value)))}</field>")

    return f"<event {attr_text}>{''.join(details)}</event>"


def build_patient_xml(person_id, visits, events_by_visit, unlinked_events):
    visit_xml = []
    sorted_visits = sorted(
        visits,
        key=lambda item: normalize_value(item.get("event_date")) or "",
    )
    for visit in sorted_visits:
        visit_id = visit.get("visit_occurrence_id")
        start = normalize_value(visit.get("event_date", ""))
        concept_name = visit.get("concept_name") or ""
        attrs = {
            "id": visit_id,
            "start": start,
            "concept_name": concept_name,
        }
        attrs = {k: v for k, v in attrs.items() if v not in (None, "")}
        attr_text = " ".join(f'{k}="{escape(str(v))}"' for k, v in attrs.items())
        event_xml = [event_to_xml("visit", visit)]
        event_xml.extend(events_by_visit.get(visit_id, []))
        visit_xml.append(f"<visit {attr_text}>{''.join(event_xml)}</visit>")

    if unlinked_events:
        visit_xml.append(f"<unlinked_events>{''.join(unlinked_events)}</unlinked_events>")

    return f"<patient id=\"{escape(str(person_id))}\">{''.join(visit_xml)}</patient>"


def materialize_batch(client, args, person_ids):
    visits_by_person = defaultdict(list)
    events_by_person_visit = defaultdict(lambda: defaultdict(list))
    unlinked_by_person = defaultdict(list)

    for config in DOMAIN_CONFIGS:
        rows = query_domain(client, args, config, person_ids)
        for row in rows:
            row_data = dict(row.items())
            person_id = int(row_data["person_id"])
            visit_id = row_data.get("visit_occurrence_id")
            if config["domain"] == "visit":
                visits_by_person[person_id].append(row_data)
            elif visit_id:
                events_by_person_visit[person_id][visit_id].append(event_to_xml(config["domain"], row_data))
            else:
                unlinked_by_person[person_id].append(event_to_xml(config["domain"], row_data))

    records = []
    for person_id in person_ids:
        xml = build_patient_xml(
            person_id,
            visits_by_person.get(person_id, []),
            events_by_person_visit.get(person_id, {}),
            unlinked_by_person.get(person_id, []),
        )
        records.append({"uid": person_id, "person_id": person_id, "text": xml})
    return records


def main():
    args = parse_args()
    client = bigquery.Client(project=args.project_id)
    person_ids = read_person_ids(args.sampled_patients_csv)
    output_path = Path(args.output_jsonl)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with open(output_path, "w", encoding="utf-8") as f:
        for batch in chunks(person_ids, args.batch_size):
            records = materialize_batch(client, args, batch)
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False))
                f.write("\n")
                written += 1
            print(f"Wrote {written}/{len(person_ids)} patient timelines")

    print(f"Saved materialized EHR JSONL to {output_path}")


if __name__ == "__main__":
    main()
