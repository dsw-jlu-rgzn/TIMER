# 无训练结构化时间问答实验

## 1. Motivation / 动机

TIMER 证明了纵向临床记录需要明确的时间锚定：模型不仅要读懂病历内容，还要知道事件发生在什么时候、哪些证据在当前时间边界内可用、患者状态如何跨多次就诊发生变化。

但是，TIMER 原始框架仍然偏文本化。它可以让模型更好地回答“患者病程如何变化”这类时间推理问题，却没有显式建模结构化数值轨迹，例如：

- 检验值变化：creatinine、glucose、hemoglobin、A1c、LDL 等。
- 药物剂量变化：insulin dose、vancomycin dose、statin dose 等。
- 生命体征趋势：blood pressure、heart rate、weight、oxygen saturation 等。
- 药物剂量和后续检验/生命体征之间的 dose-response 关系。

这个实验的核心想法是：在不训练任何模型的情况下，先验证一个最小假设：

> 如果我们把原始 EHR 中的检验值、药物剂量和日期事件抽取成结构化时间轨迹表，那么一个现成 LLM 是否能更准确地回答纵向临床问答？

这一步很适合放在 flow matching 训练之前。因为如果显式结构化轨迹不能提升问答表现，那么后续训练 flow matching 生成轨迹的收益也值得怀疑；反过来，如果结构化轨迹确实提升了 QA，那么这个表格模块就可以成为未来 flow matching 替换或增强的对象。

## 2. Research Goal / 研究目标

本实验的目标是建立一个快速、可审计、无需训练的基础闭环：

1. 使用 TIMER demo 或自己的 EHR timeline。
2. 从原始病历中抽取结构化时间证据。
3. 构造不同输入条件下的 QA prompt。
4. 使用现成 LLM 直接推理，不做 SFT，不做 LoRA。
5. 评估答案正确性、证据引用质量和时间泄漏。

这个实验不是为了证明临床可部署性，而是验证一个技术方向：

> 结构化时间轨迹是否是连接纵向 EHR 数据和 LLM 推理的有效中间表示。

## 3. What Is the Task? / 任务是什么

这是一个 **temporal EHR question answering task / 纵向 EHR 时间问答任务**。

它不是普通 summarization，也不是训练任务。每条样本包含：

- 一条患者时间线。
- 一个关于数值趋势、药物剂量变化或时间边界的临床问题。
- 一个由规则生成或人工检查的 gold label。
- 模型在固定 prompt 下生成的 JSON 回答。

示例问题：

```text
Did the patient's creatinine increase after vancomycin was started?
患者开始使用万古霉素后，肌酐是否升高？
```

模型需要输出：

```json
{
  "answer": "yes",
  "trend": "increased",
  "evidence": [
    {"date": "2024-03-03", "event": "creatinine", "value": "0.9 mg/dL"},
    {"date": "2024-03-06", "event": "creatinine", "value": "1.6 mg/dL"}
  ],
  "reason": "Creatinine rose after vancomycin initiation.",
  "temporal_boundary_followed": true
}
```

其中 `answer` 建议限定为：

```text
yes / no / unclear
```

`trend` 建议限定为：

```text
increased / decreased / unchanged / mixed / insufficient_evidence
```

## 4. 为什么这是 No-Training 实验

这个实验不会运行：

```text
timer/instruct_tune/tune_llama_recipes.py
```

也不会训练 LoRA adapter。

它只使用：

- 已有 EHR 文本。
- 规则抽取的检验值、药物剂量和简单趋势。
- Prompt 构造。
- 现成 LLM 推理。
- 规则评估或少量人工复核。

也就是说，本实验验证的是 **表示是否有用**，不是验证 **训练是否有效**。

## 5. Input Data Shape / 输入数据长什么样

最容易从 TIMER demo 格式开始：

```json
{
  "uid": 1001,
  "person_id": 1001,
  "window_index": 0,
  "text": "<patient><visit start=\"01/10/2024\"><note>...</note></visit></patient>"
}
```

对于结构化数值实验，更理想的时间线应包含日期，并至少包含以下一种信息：

- Lab values：creatinine、glucose、hemoglobin、WBC、platelets、potassium、A1c、LDL。
- Medication starts or dose changes：vancomycin、insulin、diuretics、heparin、statins、antihypertensives。
- Vitals：blood pressure、heart rate、oxygen saturation、weight。

示例：

```xml
<patient>
  <visit start="03/03/2024">
    <note>Creatinine 0.9 mg/dL. No vancomycin exposure.</note>
  </visit>
  <visit start="03/04/2024">
    <note>Vancomycin started 1000 mg IV q12h. Creatinine 1.0 mg/dL.</note>
  </visit>
  <visit start="03/06/2024">
    <note>Creatinine increased to 1.6 mg/dL. Vancomycin continued.</note>
  </visit>
</patient>
```

## 6. Proposed Pilot Tasks / 建议的基础任务

先做 4 类任务，每类 25 条样本，组成 100 条 pilot set。

### 6.1 Lab Trend QA / 检验趋势问答

问题形式：

```text
Did {lab_name} increase, decrease, or remain unchanged between {start_date} and {end_date}?
```

示例：

```text
Did creatinine increase from 03/03/2024 to 03/06/2024?
```

简单 gold label 规则：

```text
if post_mean > pre_mean * 1.2: increased
elif post_mean < pre_mean * 0.8: decreased
else: unchanged
```

### 6.2 Medication Start to Lab Response QA / 药物开始后的检验反应问答

问题形式：

```text
After {medication} was started, did {lab_name} worsen, improve, or remain unchanged?
```

示例：

```text
After vancomycin was started, did creatinine worsen?
After insulin was intensified, did glucose improve?
```

规则思路：

```text
pre_window = 用药开始前的 lab values
post_window = 用药开始后的 lab values
比较 pre_mean 和 post_mean
```

对于 creatinine：

```text
increase = worse
decrease = improved
```

对于 glucose：

```text
高基线后的 decrease = improved
明显 increase = worse
```

### 6.3 Dose-Response QA / 剂量反应问答

问题形式：

```text
After the dose of {medication} increased, did the relevant clinical marker move in the expected direction?
```

示例：

```text
After insulin dose increased, did glucose decrease?
After atorvastatin was started, did LDL decrease?
After diuretic dose increased, did potassium decrease or creatinine rise?
```

这一任务比单纯趋势判断更难，因为模型需要把药物剂量变化和后续指标变化对齐。

### 6.4 Temporal Boundary QA / 时间边界问答

问题形式：

```text
Using only evidence before {boundary_date}, answer whether {clinical_marker} had improved.
```

这个任务测试模型是否会偷看未来信息。

示例：

```text
Using only evidence before 03/05/2024, did creatinine worsen after vancomycin was started?
```

如果唯一的高肌酐值出现在 03/06/2024，那么正确答案应该是：

```text
unclear
```

## 7. Experimental Conditions / 实验条件

对同一批问题构造四种输入条件。

### A. Raw Timeline Only

只给模型原始 EHR 文本。

目的：

```text
作为 TIMER-style baseline。
```

### B. Raw Timeline + Structured Trajectory Table

给模型原始 EHR 文本，以及结构化检验值/药物剂量/趋势表。

目的：

```text
主实验条件，验证结构化时间证据是否有帮助。
```

### C. Structured Trajectory Table Only

不给原始病历，只给结构化时间轨迹表。

目的：

```text
测试结构表本身是否已经包含足够信号。
```

### D. Shuffled Raw Timeline + Structured Trajectory Table

打乱原始 visit 顺序，但结构化表保持正确时间排序。

目的：

```text
压力测试。如果表现仍然接近 B，说明模型主要依赖结构化表，而不是原始文本顺序。
```

## 8. Structured Trajectory Table Format / 结构化轨迹表格式

第一版用纯文本表格即可。

```text
Structured temporal evidence:

Medication events:
| date | medication | dose | route | frequency | change_type |
| 2024-03-04 | vancomycin | 1000 mg | IV | q12h | started |

Lab trajectory:
| date | lab | value | unit | relation_to_med |
| 2024-03-03 | creatinine | 0.9 | mg/dL | before |
| 2024-03-04 | creatinine | 1.0 | mg/dL | same_day |
| 2024-03-06 | creatinine | 1.6 | mg/dL | after |

Computed trend:
| target | pre_mean | post_mean | relative_change | direction |
| creatinine_after_vancomycin | 0.95 | 1.60 | +68.4% | increased |
```

第一版可以用正则和简单规则生成，不需要完美。最重要的是先验证：即使结构化表不完美，是否仍然比 raw text 更能帮助 LLM。

## 9. Prompt Template / Prompt 模板

建议所有条件使用同一个严格模板。

```text
You are answering a temporal question about a longitudinal clinical record.

Rules:
1. Use only the provided patient record and structured evidence.
2. Respect the temporal boundary if one is given.
3. Do not use evidence after the boundary date.
4. If the evidence is insufficient, answer "unclear".
5. Return valid JSON only.

Question:
{question}

Patient record:
{raw_timeline_or_empty}

Structured evidence:
{structured_table_or_empty}

Return JSON with this schema:
{
  "answer": "yes/no/unclear",
  "trend": "increased/decreased/unchanged/mixed/insufficient_evidence",
  "evidence": [
    {"date": "YYYY-MM-DD", "event": "...", "value": "..."}
  ],
  "reason": "...",
  "temporal_boundary_followed": true
}
```

## 10. Evaluation Metrics / 评估指标

### 10.1 Answer Accuracy

比较 `answer` 或 `trend` 是否和 gold label 一致。

推荐指标：

```text
Accuracy and Macro-F1
```

Macro-F1 很重要，因为 `unclear` 和 `unchanged` 可能是少数类。

### 10.2 Evidence Precision

检查模型引用的日期和值是否真实出现在输入中。

简单规则：

```text
evidence_precision = cited_supported_evidence / all_cited_evidence
```

### 10.3 Temporal Leakage Rate

对于 boundary task，检查模型是否引用或使用了 boundary date 之后的证据。

```text
leakage_rate = examples_with_future_evidence / boundary_examples
```

### 10.4 JSON Validity

检查输出是否是合法 JSON，是否满足 schema。

```text
format_success_rate = valid_json_outputs / all_outputs
```

### 10.5 Abstention Quality

对于证据不足的问题，检查模型是否回答 `unclear`。

```text
unclear_accuracy = correct_unclear_outputs / insufficient_evidence_examples
```

## 11. Expected Minimal Result Table / 预期结果表

第一版实验报告可以长这样：

| Condition | Accuracy | Macro-F1 | Evidence Precision | Leakage Rate | JSON Validity |
| --- | ---: | ---: | ---: | ---: | ---: |
| A. Raw timeline only | TBD | TBD | TBD | TBD | TBD |
| B. Raw + structured table | TBD | TBD | TBD | TBD | TBD |
| C. Structured table only | TBD | TBD | TBD | TBD | TBD |
| D. Shuffled raw + structured table | TBD | TBD | TBD | TBD | TBD |

最关键的比较是：

```text
B > A
```

如果 C 接近 B，说明结构化轨迹表承载了大部分有效信号。如果 D 接近 B，说明结构化表能抵抗原始文本中的时间顺序噪声。

## 12. Suggested File Layout / 建议文件结构

建议把新实验和 TIMER 原始训练代码分开：

```text
experiments/no_training_structured_qa/
  README.md
  data/
    examples.jsonl
    prompts.csv
    gold_labels.jsonl
  outputs/
    raw_timeline_only.csv
    raw_plus_structured.csv
    structured_only.csv
    shuffled_raw_plus_structured.csv
  scripts/
    build_examples.py
    build_prompts.py
    run_inference_openai.py
    evaluate_outputs.py
```

当前 TIMER 仓库已有：

```text
timer/evaluate/inference.py
```

这个脚本可以读取包含 `instruction_id`、`patient_id`、`prompt` 的 CSV，并调用模型推理。快速启动时，可以先生成兼容它的 `prompts.csv`，不启用 LoRA。

## 13. Minimal Data Example / 最小数据样例

`examples.jsonl` 中的一条样本可以长这样：

```json
{
  "example_id": "demo_vanc_cr_001",
  "patient_id": "demo_001",
  "question": "After vancomycin was started, did creatinine increase?",
  "boundary_date": null,
  "raw_timeline": "<patient><visit start=\"2024-03-03\"><note>Creatinine 0.9 mg/dL.</note></visit><visit start=\"2024-03-04\"><note>Vancomycin started 1000 mg IV q12h. Creatinine 1.0 mg/dL.</note></visit><visit start=\"2024-03-06\"><note>Creatinine increased to 1.6 mg/dL.</note></visit></patient>",
  "structured_evidence": {
    "medication_events": [
      {
        "date": "2024-03-04",
        "medication": "vancomycin",
        "dose": "1000 mg",
        "route": "IV",
        "frequency": "q12h",
        "change_type": "started"
      }
    ],
    "lab_events": [
      {"date": "2024-03-03", "lab": "creatinine", "value": 0.9, "unit": "mg/dL", "relation_to_med": "before"},
      {"date": "2024-03-04", "lab": "creatinine", "value": 1.0, "unit": "mg/dL", "relation_to_med": "same_day"},
      {"date": "2024-03-06", "lab": "creatinine", "value": 1.6, "unit": "mg/dL", "relation_to_med": "after"}
    ],
    "computed_trend": {
      "target": "creatinine_after_vancomycin",
      "pre_mean": 0.95,
      "post_mean": 1.6,
      "relative_change": 0.684,
      "direction": "increased"
    }
  },
  "gold": {
    "answer": "yes",
    "trend": "increased"
  }
}
```

## 14. How This Connects to Flow Matching / 和 Flow Matching 的关系

无训练版本使用规则生成结构表：

```text
raw EHR -> regex/rules -> structured trajectory table -> LLM answer
```

后续 flow matching 版本可以替换或增强这个结构表：

```text
raw EHR -> structured event extraction -> conditional flow matching -> imputed/forecast/counterfactual trajectory -> LLM answer
```

可能的 flow matching 目标包括：

- 缺失检验值补全。
- 短期 lab trajectory forecasting。
- 药物剂量-反应轨迹生成。
- 不同剂量方案下的 counterfactual trajectory。

因此，这个 no-training pilot 的作用是先验证下游接口，再决定是否值得训练 flow matching。

## 15. Success Criteria / 成功标准

如果出现以下至少一种结果，就值得继续扩展：

- Raw + structured table 相比 raw timeline only 提升答案准确率。
- Raw + structured table 降低 temporal leakage。
- Structured table only 接近 Raw + structured table。
- Evidence precision 提升，即使答案准确率提升不大。

最理想的早期结果是：

```text
Structured evidence improves both answer accuracy and evidence precision while reducing temporal leakage.
```

## 16. First Implementation Plan / 第一轮实现计划

建议第一轮：

1. 构造 20 条 synthetic 或人工整理样本，覆盖 creatinine、glucose、LDL、A1c、hemoglobin。
2. 为 A、B、C 三个条件构造 prompts。
3. 用一个现成 LLM 跑推理。
4. 人工检查 10 条输出，确认 JSON schema 可用。
5. 对全部样本做自动评分。
6. 等 schema 和指标稳定后，再扩展到 100 条样本。

第一步不要急着做大规模数据管线。目标是先做一个干净、可证伪的最小实验。

