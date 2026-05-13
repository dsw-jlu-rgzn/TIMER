# 无训练结构化时间问答实验

## Motivation / 动机

TIMER 证明了纵向 EHR 推理需要 temporal grounding：模型需要知道临床事件发生在什么时候、哪些证据属于当前时间窗口、患者状态如何跨多次就诊变化。

但是 TIMER 原始框架仍然更偏文本化。它可以提升纵向临床问答能力，但没有显式建模结构化数值信号，例如 lab values、medication doses、vital signs 和 dose-response trajectories。

这个实验先问一个很小但关键的问题：

> 在不训练任何模型的情况下，给 LLM 增加结构化时间轨迹表，是否能提升它对检验趋势、药物剂量变化和时间边界问题的回答质量？

如果答案是肯定的，后续就可以把规则生成的结构化表替换成 flow matching 模块，用于缺失值补全、轨迹预测和反事实剂量-反应模拟。

## Goal / 目标

构建一个 no-training baseline，对比：

```text
raw TIMER-style EHR prompt
vs.
raw EHR + structured temporal evidence
```

本实验不追求临床部署，只验证：

> 结构化数值轨迹是否是连接纵向 EHR 和 LLM 推理的有效中间表示。

## Task Definition / 任务定义

这是一个 **temporal EHR question answering task / 纵向 EHR 时间问答任务**。

每条样本包含：

- `raw_timeline`：带日期的患者纵向记录。
- `structured_evidence`：抽取出的检验值、药物剂量事件和计算出的趋势。
- `question`：一个临床时间问题。
- `gold`：规则生成或人工检查的标签。

模型需要返回 JSON：

```json
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

注意：这不是 summarization，而是 constrained QA。正确性依赖事件顺序、数值趋势和可选时间边界。

## Pilot Task Families / 试验任务类型

### 1. Lab Trend QA / 检验趋势问答

问题：

```text
Did {lab_name} increase, decrease, or remain unchanged between {start_date} and {end_date}?
```

示例：

```text
Did creatinine increase from 2024-03-03 to 2024-03-06?
```

简单 gold 规则：

```text
if post_mean > pre_mean * 1.2: increased
elif post_mean < pre_mean * 0.8: decreased
else: unchanged
```

### 2. Medication Start to Lab Response QA / 用药后的检验反应问答

问题：

```text
After {medication} was started, did {lab_name} worsen, improve, or remain unchanged?
```

示例：

```text
After vancomycin was started, did creatinine worsen?
After insulin was intensified, did glucose improve?
```

### 3. Dose-Response QA / 剂量反应问答

问题：

```text
After the dose of {medication} increased, did the relevant clinical marker move in the expected direction?
```

示例：

```text
After insulin dose increased, did glucose decrease?
After atorvastatin was started, did LDL decrease?
```

### 4. Temporal Boundary QA / 时间边界问答

问题：

```text
Using only evidence before {boundary_date}, answer whether {clinical_marker} had improved.
```

这个任务测试模型是否会偷看未来信息。如果关键检验结果出现在 boundary date 之后，模型应该回答 `unclear`。

## Experimental Conditions / 实验条件

对同一批样本跑四种输入条件：

| Condition | Input | Purpose |
| --- | --- | --- |
| A | Raw timeline only | TIMER-style baseline |
| B | Raw timeline + structured trajectory table | 主实验条件，测试结构化证据是否有帮助 |
| C | Structured trajectory table only | 测试结构表本身是否已经足够 |
| D | Shuffled raw timeline + structured trajectory table | 压力测试，看结构表是否能抵抗原始文本顺序噪声 |

关键比较：

```text
B vs A
```

如果 B 明显优于 A，说明结构化时间证据有价值。如果 C 接近 B，说明结构化表承载了主要有效信息。

## Structured Evidence Format / 结构化证据格式

第一版用纯文本表格即可：

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

## Prompt Template / Prompt 模板

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

## Minimal Example / 最小样例

```json
{
  "example_id": "demo_vanc_cr_001",
  "patient_id": "demo_001",
  "question": "After vancomycin was started, did creatinine increase?",
  "boundary_date": null,
  "raw_timeline": "<patient><visit start=\"2024-03-03\"><note>Creatinine 0.9 mg/dL.</note></visit><visit start=\"2024-03-04\"><note>Vancomycin started 1000 mg IV q12h. Creatinine 1.0 mg/dL.</note></visit><visit start=\"2024-03-06\"><note>Creatinine increased to 1.6 mg/dL.</note></visit></patient>",
  "structured_evidence": {
    "medication_events": [
      {"date": "2024-03-04", "medication": "vancomycin", "dose": "1000 mg", "route": "IV", "frequency": "q12h", "change_type": "started"}
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

## Evaluation Metrics / 评估指标

第一版先用自动指标：

- `answer_accuracy`：`answer` 是否和 gold 一致。
- `trend_accuracy`：`trend` 是否和 gold 一致。
- `macro_f1`：处理类别不均衡。
- `evidence_precision`：引用的日期和值是否真实出现在输入中。
- `temporal_leakage_rate`：boundary task 中是否引用或使用了未来证据。
- `json_validity`：输出是否能解析成 JSON，并符合 schema。
- `unclear_accuracy`：证据不足时是否回答 `unclear`。

## Expected Result Table / 结果表

| Condition | Accuracy | Macro-F1 | Evidence Precision | Leakage Rate | JSON Validity |
| --- | ---: | ---: | ---: | ---: | ---: |
| A. Raw timeline only | TBD | TBD | TBD | TBD | TBD |
| B. Raw + structured table | TBD | TBD | TBD | TBD | TBD |
| C. Structured table only | TBD | TBD | TBD | TBD | TBD |
| D. Shuffled raw + structured table | TBD | TBD | TBD | TBD | TBD |

## How This Connects to Flow Matching / 和 Flow Matching 的关系

No-training 版本使用规则：

```text
raw EHR -> regex/rule extraction -> structured trajectory table -> LLM QA
```

后续 flow matching 版本可以替换或增强结构表：

```text
raw EHR -> structured event extraction -> conditional flow matching -> imputed/forecast/counterfactual trajectory -> LLM QA
```

可能的 flow matching 目标：

- Missing lab imputation / 缺失检验值补全。
- Short-horizon lab forecasting / 短期检验轨迹预测。
- Medication dose-response generation / 药物剂量反应生成。
- Counterfactual trajectory under alternate dose schedules / 不同剂量方案下的反事实轨迹。

这个 pilot 的价值是：先验证 LLM 是否能利用结构化轨迹，再决定是否值得训练 flow matching。

## First Milestone / 第一阶段里程碑

先构造 20 条样本：

- 5 条 creatinine / nephrotoxic medication 样本。
- 5 条 glucose / insulin 样本。
- 5 条 LDL 或 A1c treatment-response 样本。
- 5 条 temporal-boundary 样本，刻意设计证据不足或未来证据陷阱。

第一轮只跑 A、B、C 三组。等 prompt 和评估脚本稳定后，再加 D。

成功标准：

```text
Raw + structured evidence 相比 Raw timeline only，在答案准确率、证据精确率或 temporal leakage 上至少有一项明显改善。
```

