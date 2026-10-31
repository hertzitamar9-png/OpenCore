---
language:
  - en
  - it
  - pt
  - es
  - de
  - fr

license: apache-2.0
library_name: transformers

tags:
  - Velvet2
  - AlmawaveLabs
  - text-generation
  - function-calling
  - instruction-tuned
  - multilingual

pipeline_tag: text-generation


extra_gated_description: >-
  If you want to learn more about how we process your personal data,
  please read our
  <a href="https://www.almawavelabs.com/privacy-policy/">Privacy Policy</a>.

---


# Velvet2-3B

## Introducing Velvet2

Velvet2 is the new generation of state of the art models developed by Almawave Labs, designed to provide a unified platform for multilingual, multimodal, and enterprise AI applications.

Built from scratch and trained on the Leonardo supercomputing infrastructure operated by CINECA, the Velvet2 family introduces substantial advances in language understanding, structured generation, long-context processing, reasoning and multimodal intelligence, while maintaining a strong focus on deployment efficiency and real-world enterprise requirements.

The Velvet2 family is designed around a common technological foundation and will be released in multiple configurations, spanning compact language models, multimodal models, and larger mixture-of-experts architectures. This approach enables organizations to adopt the most suitable model for their latency, cost, and capability requirements while maintaining architectural consistency across deployments.

Depending on model size and target capabilities, Velvet2 models are pretrained on datasets ranging from approximately 5 trillion to over 8 trillion tokens, followed by extensive post-training pipelines focused on instruction following, reasoning, structured outputs, alignment, and safety.


## Velvet2-3B

Velvet2-3B is a compact, text-only instruction-tuned language model belonging to the Velvet2 family. It is optimized to provide a compelling combination of capability, latency, and serving efficiency, making it well suited for production-grade applications and enterprise deployments.

Trained from scratch on approximately 5 trillion tokens of carefully curated public data, Velvet2-3B leverages a curriculum-based pretraining strategy designed to progressively develop linguistic competence, reasoning skills, and long-context capabilities.

The model was further refined through a large-scale post-training pipeline based on approximately 3.5 million human-annotated and synthetic instruction examples. This stage combined supervised fine-tuning and preference optimization techniques to improve alignment, instruction adherence, conversational quality, structured generation, multilingual performance, and overall robustness.

With native support for six European languages, Velvet2-3B sets a new quality benchmark within the Velvet family, achieving substantial gains over previous generations in reasoning, instruction following, structured outputs, few-shot learning, multilingual interaction, and long-context comprehension.


Compared with the previous Velvet generation, Velvet2-3B provides substantial improvements in:

- Instruction following;
- Multi-turn conversation;
- Long-form content creation;
- Table and structured data understanding;
- JSON and structured output generation;
- Agentic and RAG workflows;
- Few-shot learning;
- Robustness across diverse system prompts.

Velvet2-3B supports a native context length of 32,768 tokens, which can be extended to 131,072 tokens using YaRN, enabling robust long-context reasoning across document-intensive workflows, Retrieval-Augmented Generation (RAG), information extraction, and extended multi-turn conversations.


## Model Details

| Property | Value |
|---|---|
| Developer | Almawave Labs|
| Model family | Velvet2 |
| Model name | Velvet2-3B |
| Model type | Dense, decoder-only Transformer |
| Model stage | Instruction-tuned |
| Parameter count | 3B |
| Input modality | Text |
| Output modality | Text |
| Pretraining data | Approximately 5 trillion tokens |
| Training sequence length | Up to 16K tokens |
| Maximum supported context | Up to 32K tokens |
| Vocabulary size | 127K |
| Languages | Italian, English, Spanish, Portuguese, French, German |
| Release date | October 2026 |
| License | Apache License 2.0 |
| Code of practice | Almawave Labs is signatory of the EU GPAI Code of Practice, see https://digital-strategy.ec.europa.eu/en/policies/contents-code-gpai





## Architecture

Velvet2-3B is a dense autoregressive language model based on a decoder-only Transformer architecture optimized for efficient inference and multilingual language understanding.

| Component | Configuration |
|------------|-------------|
| Parameters | 3B |
| Layers | 38 |
| Hidden Dimension | 2560 |
| Intermediate Size | 8960 |
| Attention Mechanism | Grouped Query Attention (GQA) |
| Query Heads | 40 |
| Key-Value Heads | 10 |
| Activation Function | SiLU |
| Normalization | RMSNorm |
| Positional Encoding | Rotary Position Embeddings (RoPE) |
| Vocabulary Size | 127K |
| Training Context Length | 16K Tokens |
| Supported Context Length | Up to 32K Tokens |


Grouped Query Attention (GQA) improves inference efficiency by reducing the number of key-value heads while preserving model quality and enabling more efficient KV cache utilization.

The model uses the Velvet-14B tokenizer, comprising a vocabulary of approximately 127K tokens designed to support multilingual training and European language diversity.

## Training

### Pretraining

Velvet2-3B was pretrained on approximately 5 trillion tokens collected from publicly available sources.

The training corpus underwent an extensive curation pipeline including:

- quality filtering;
- language identification;
- deduplication;
- safety filtering;
- multilingual balancing.

Particular attention was devoted to maintaining broad and balanced coverage across the model's primary languages while preserving linguistic diversity and overall data quality.

The pretraining process follows a curriculum learning strategy composed of successive training stages, including large-scale foundational pretraining, capability refinement, annealing, and long-context adaptation. This progressive schedule enables the model to incrementally acquire linguistic knowledge, improve convergence quality, and extend context handling capabilities up to 32K tokens.

#### Data Freshness

The pretraining data cutoff spans the period between December 2025 and January 2026.

---

### Post-Training

Velvet2-3B was further optimized through a multi-stage post-training pipeline comprising approximately 3.5 million human-annotated and synthetic instruction examples.

The post-training process follows a multi-stage alignment strategy integrating supervised refinement and reinforcement learning. This progressive optimization framework enhances CoT performance, instruction adherence, and alignment with human preferences.
Together, these stages were designed to improve instruction following, conversational quality, CoT abilities, structured output generation, multilingual interactions, and robustness across a wide range of prompting styles.

The post-training corpus combines permissively licensed public instruction datasets with internally generated synthetic datasets specifically designed for enterprise-oriented language tasks.

---

## Supported Languages

Velvet2-3B was primarily optimized for:

- Italian
- English
- French
- German
- Spanish
- Portuguese

To ensure high-quality multilingual performance, the dataset was curated to balance linguistic and cultural representation, reducing overfitting biases. Velvet2-3B uses the Almawave/Velvet-14B tokenizer (127K vocabulary size). While the model may demonstrate capabilities in additional languages, performance has been systematically optimized and evaluated only for the languages listed above.

---

## Intended Use

Velvet2-3B is intended as a general-purpose multilingual language model for enterprise and research applications.

Representative deployment scenarios include:

- Conversational AI and virtual assistants
- Enterprise search and knowledge management
- Retrieval-Augmented Generation (RAG) systems
- Customer support automation
- Document analysis and processing
- Information extraction pipelines
- Content generation and editing
- Structured data transformation
- Workflow automation
- Agentic applications and tool-augmented systems

Depending on the target use case, additional prompting strategies, grounding mechanisms, safety controls, or task-specific adaptation may be required.

---

### Capabilities

Velvet2-3B has been optimized for the following capabilities:

- Instruction Following
- Multi-Turn Dialogue
- Long-Context Understanding
- Information Extraction
- Structured Output Generation
- JSON and Schema-Constrained Generation
- Table and Semi-Structured Data Understanding
- Function Calling
- Summarization
- Question Answering
- Retrieval-Augmented Generation (RAG)
- Multilingual Text Generation
- CoT Tasks
- Few-Shot Generalization

---

## Evaluation

Velvet2-3B was evaluated across a diverse set of benchmarks covering knowledge, instruction following, reasoning, long-context understanding, function calling, multilingual capabilities, and safety.
 
The evaluation suite combines English, Italian, and multilingual benchmarks, with particular focus on the six primary languages supported by the model.


---

### Evolution Across Velvet Generations

The table below highlights the progression from previous Velvet models to Velvet2-3B across selected benchmarks representative of core model capabilities.


| Benchmark           | Velvet2-3B | Velvet-14B | Velvet1.5-2B |
| ------------------- | ---------: | ---------: | -----------: |
| MMLU (5-shot)       |      71.54 |       56.4 |         44.6 |
| IFEval              |      74.2 |       65.4 |         53.6 |
| GSM8K               |      60.27 |       53.2 |         23.0 |
| Hellaswag (10-shot) |       67.8 |       72.8 |         64.6 |
| ARC-Challenge       |       57.9 |       57.3 |         48.7 |
| TruthfulQA          |      76.41 |       73.2 |         50.5 |



### Core Capabilities

| Capability | Benchmark | Velvet2-3B | Ministral 3 3B | Llama 3.2 3B |  Qwen2.5-3B |  Tiny-aya-water | Phi-4-mini 3B |
|------------|-----------|------------:|---------------:|-------------:|--------------:|-----------:|----:|
| General Knowledge | MMLU (5-shot) | 71.54 | 69.2 | 59.7 | 66.4  | 56,0 |  68.6 |
| General Knowledge | MMLU-Pro (5-shot) | 32.3 | 35.1 | 31.6 | 32.9 |30.3 |  39.1 |
| Instruction Following | IFEval (inst_level_strict_acc) | 74.2 | 66.4 | 77.6 |  69.4 | 84,1 |  78.9 |
| Commonsense Reasoning | HellaSwag | 72.8 | 73.0 | 70.5 | 74.8 |   66.5 |  72.7 |
| Mathematical Reasoning | GSM8K (5-shot) | 62.9 | 74.8 | 65.6 | | 62.4 | 53.4 |  82.0
| Complex Reasoning | BBH (3-shot) | 51.8 | 49.2 | 46.0 | 46.6 | 43.4 | 56.4 |
| Function Calling | BFCL-v3 | 39.47 | 51.5 | 45.87 |  22.21 | 24.64| 39.5 |
| Safety | TruthfulQA | 76.41 | 51.6 | 46.2 |  52.3 | 47.5 |  52.6 |



### Multilingual Evaluation (EuroEval) - accuracy

| Model | Italian | English |Portuguese | French | Spanish | German |
|---------|---------|---------|---------|---------|---------|---------|
| Velvet2-3B | 52.68 | 56.58 |51.16 | 47.40 | 51.41 | 46.31 |
| Ministral 3 3B | 52.93 | 56.17 | 51.2 | 53.77 | 50.47 | 44.06 |
| Llama 3.2 3B | 42.33 |51.99| 47.83 | 45.92 | 42.83 | 42.41 |
| Qwen2.5-3B | 51.63 |55.28| 53.32 | 47.68 |45.09 | 45.08 |
| Tiny-aya-water | 46.56 | 52.32 |54.57 | 40.742 | 43.61 | 40.03 |
| Phi-4-mini 3B |  50.54 |  58.03 | 53.44   | 47.95 | 51.83  | 46.92 |
| Granite-4.1 | 50.31| 50.32  | 46.33 | 54.72 |39.87 | 41.75|


### Italian Evaluation

| Benchmark | Velvet2-3B | Ministral 3 3B | Llama 3.2 3B |  Qwen2.5-3B| Tiny-aya-water| Phi-4-mini 3B | Granite-4.1 |
|------------|------------|------------|------------|------------|------------|---|--|
| MMLU-IT  (5-shot)          | 60.8 | 61.5 |  53.9 |  58.8  |52.7 | 58.0 | 58.8 |
| HellaSwag-IT (10-shot)     | 62.9 | 61.4 |  54.7 |  56.0 |58.3 | 57.9 | 55.6 |
| ARC-Challenge IT (25-shot) | 57.9 | 57.8 |  45.6 |  41.6 | 41.1 | 44.8 | 48.6 |



## Usage

The model can be used with the following frameworks;

<details>
<summary> vllm</summary>

```python
from openai import OpenAI

client = OpenAI(
    api_key="none",
    base_url="http://localhost:8000/v1"
)

response = client.chat.completions.create(
    model="AlmawaveLabs/Velvet2-3B",
    messages=[{"role": "user", "content": "Plan me an 3-days itinerary that captures the Eternal City’s most legendary sites"}]
)

print(response.choices[0].message.content)
```

### Tool Calling

### Start the Server

Tool calling requires the model's companion files, `tool_parser_plugin.py` and `tool_chat_template.jinja`. Download them from the model repository into the directory where you will start vLLM:

```bash
hf download Almawave/Velvet2-3B \
    tool_parser_plugin.py tool_chat_template.jinja \
    --local-dir .
```

Start vLLM with automatic tool selection, the registered `velvet` parser, and the bundled chat template:

```bash
vllm serve Almawave/Velvet2-3B \
    --served-model-name Almawave/Velvet2-3B \
    --host 127.0.0.1 \
    --port 8000 \
    --dtype bfloat16 \
    --enable-auto-tool-choice \
    --tool-call-parser velvet \
    --tool-parser-plugin ./tool_parser_plugin.py \
    --chat-template ./tool_chat_template.jinja
```

The parser name `velvet` must match the name registered by the plugin. This setup was tested with vLLM 0.19.1. Compatibility with other vLLM releases may require verification.

### Python Example (OpenAI SDK)

Install the client with `pip install openai`, then run this example against the local vLLM endpoint:

```python
import json

from openai import OpenAI

MODEL = "Almawave/Velvet2-3B"
client = OpenAI(base_url="http://localhost:8000/v1", api_key="EMPTY")

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather_forecast",
            "description": "Get the weather forecast for a city on a given date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name."},
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format.",
                    },
                },
                "required": ["location", "date"],
            },
        },
    }
]


def get_weather_forecast(location, date):
    # Mock implementation; replace with your own tool.
    return {
        "location": location,
        "date": date,
        "forecast": "Sunny, 18°C (64.4°F)",
    }


available_tools = {"get_weather_forecast": get_weather_forecast}

messages = [

    {
        "role": "user",
        "content": "What's the weather forecast in Rome on November 6, 2026?",
    },
]

response = client.chat.completions.create(
    model=MODEL,
    messages=messages,
    tools=tools,
    temperature=0.0,
    max_tokens=256,
)
assistant_message = response.choices[0].message

if assistant_message.tool_calls:
    # Preserve the assistant tool-call message in conversation history.
    messages.append(assistant_message)

    for tool_call in assistant_message.tool_calls:
        function = available_tools.get(tool_call.function.name)
        if function is None:
            raise ValueError(f"Unknown tool: {tool_call.function.name}")

        arguments = json.loads(tool_call.function.arguments)
        result = function(arguments)
        print(f"Tool call: {tool_call.function.name}({arguments})")

        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(result, ensure_ascii=False),
            }
        )

    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=tools,
        temperature=0.0,
        max_tokens=256,
    )

print(f"Assistant: {response.choices[0].message.content}")
```



</details>





<details>
<summary> transformers </summary>

```python
import torch
from transformers import AutoModel, AutoTokenizer

model_id = "AlmawaveLabs/Velvet2-3B"
tokenizer = AutoTokenizer.from_pretrained(model_id)
model = AutoTokenizer.from_pretrained(model_id,dtype=torch.bfloat16).to(device).eval()

messages = [

    {"role": "user", "content": "Plan me an 3-days itinerary that captures the Eternal City’s most legendary sites"},
]

model_inputs = tokenizer.apply_chat_template(
    messages, tokenize=True, return_tensors="pt"
)

generated_output = model.generate(model_inputs, max_new_tokens=128,)
output_text=
    tokenizer.decode(
        outputs[0][generated_output[0].shape[-1] :], skip_special_tokens=True
    )

```

### Tool Calling

With Transformers, load the tokenizer and model directly. The tokenizer's native chat template injects the supplied tools; no vLLM server or vLLM parser plugin is needed. In this path, tool definitions use the function-schema form accepted by `tokenizer.apply_chat_template`, without the OpenAI API's outer `{"type": "function", "function": ...}` wrapper.

Install the relevant libraries with `pip install transformers accelerate`.

### Python Example (Transformers)

```python
import json
import re

from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL = "Almawave/Velvet2-3B"

tools = [
    {
        "name": "get_weather_forecast",
        "description": "Get the weather forecast for a city on a given date.",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City name."},
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format.",
                },
            },
            "required": ["location", "date"],
        },
    }
]


def get_weather_forecast(location, date):
    # Mock implementation; replace with your own tool.
    return {
        "location": location,
        "date": date,
        "forecast": "Sunny, 18°C (64.4°F)",
    }


available_tools = {"get_weather_forecast": get_weather_forecast}

tokenizer = AutoTokenizer.from_pretrained(MODEL)
model = AutoModelForCausalLM.from_pretrained(
    MODEL, dtype="auto", device_map="auto"
)


def generate(messages):
    input_ids = tokenizer.apply_chat_template(
        messages,
        tools=tools,
        add_generation_prompt=True,
        return_tensors="pt",
    ).to(model.device)

    output_ids = model.generate(
        input_ids,
        attention_mask=input_ids.new_ones(input_ids.shape),
        pad_token_id=tokenizer.eos_token_id,
        max_new_tokens=256,
        do_sample=False,
    )

    # Preserve the special tool-call markers while decoding.
    text = tokenizer.decode(
        output_ids[0, input_ids.shape[1]:],
        skip_special_tokens=False,
    )
    return text.removesuffix(tokenizer.eos_token).strip()


messages = [
    {
        "role": "system",
        "content": "You are Velvet2-3B, an LLM created by Almawave Labs.",
    },
    {
        "role": "user",
        "content": "What's the weather forecast in Rome on November 6, 2026?",
    },
]

response = generate(messages)
match = re.search(r"<tool_call>(.*?)</tool_call>", response, re.DOTALL)

if match:
    # Keep the original assistant output in the conversation.
    messages.append({"role": "assistant", "content": response})

    for tool_call in json.loads(match.group(1)):
        function = available_tools.get(tool_call["name"])
        if function is None:
            raise ValueError(f"Unknown tool: {tool_call['name']}")

        result = function(tool_call["arguments"])
        print(f"Tool call: {tool_call['name']}({tool_call['arguments']})")
        messages.append(
            {
                "role": "tool",
                "content": json.dumps(result, ensure_ascii=False),
            }
        )

    response = generate(messages)

print(f"Assistant: {response}")
```





</details>

<details>
<summary> ollama</summary>
Support for GGUF format will be available soon.
</details>


### Recommended Generation Settings

The following parameters provide a good starting point for general-purpose applications:

```python
temperature = 0.0
top_p = 1.0
repetition_penalty = 0.0
frequence_penalty = 0.0
```

Application-specific tuning may further improve generation quality and consistency.


### Processing Long Texts

The model natively supports a context length of 32,768 tokens. For longer sequences, it can be extended using YaRN, a RoPE scaling method that allows efficient long-context inference while preserving performance. In frameworks that support YaRN, the following configuration can be added to config.json:

```json
{
    "rope_parameters":{
		"rope_type":"yarn",
		"factor":4,
		"original_max_position_embeddings": 32768,
		"rope_theta":path_al_config.json
	}
}
```

---


## Responsible AI

Almawave Labs is signatory of the EU GPAI Code of Practice, see https://digital-strategy.ec.europa.eu/en/policies/contents-code-gpai

Velvet2-3B incorporates safety measures throughout dataset preparation, training, and post-training stages.

These measures include data filtering, quality controls, alignment-oriented instruction tuning, and safety-focused evaluation procedures.

Despite these efforts, no language model can guarantee complete prevention of harmful, inappropriate, or inaccurate outputs. Developers are responsible for implementing application-specific safeguards and conducting appropriate risk assessments before deploying the model in production environments.

Large language models are versatile technologies designed to serve a wide range of applications. However, they are not intended to meet every developer's safety requirements out-of-the-box, as these requirements naturally vary depending on the specific use case and application context.

### Safety

For our instruction-trained model, we have undertaken comprehensive exercises, engaged in adversarial internal and external evaluations, and put into place mitigation techniques to reduce risks. These exercises were designed to thoroughly examine the model's limitations and potential, simulating real and hypothetical scenarios where undesirable behavior might arise.

However, despite these efforts, it is inevitable that some residual hazards are bound to exist, as every large language model presents intrinsic complexities that cannot be completely eliminated.

Then, developers are advised to implement suitable safety measures and exercise due diligence, tailoring these safeguards to align with their product policies and the specific requirements of their applications.

Some trade-offs between model helpfulness and alignment are likely inevitable. Developers should thoughtfully balance the benefits of alignment and helpfulness for their specific applications and audiences. They must also remain aware of residual risks when using Velvet models and leverage additional safety tools as necessary to achieve an appropriate safety standard for their use case. We advise developers to carefully evaluate risks in the context of their specific use case. They should consider the potential implications of a model failure in their applications and put adequate measures in place to manage such eventualities.

In parallel, we are collaborating with the scientific and industrial community to establish AI safety benchmark standards that are transparent, rigorous, and interpretable. The goal is to promote a better understanding of the risks associated with large language models and support the development of safer and more responsible solutions.

### Governance and Internal Oversight

Almawave Labs has established an internal governance framework for the management and continuous oversight of the Velvet model family. Key governance elements include:

Supervision by an Ethical and Technical Committee to ensure the model aligns with principles of transparency, fairness, and safety.
Ongoing bias monitoring through auditing tools, with iterative updates to improve alignment with ethical guidelines.
Restrictions on commercial and institutional usage to ensure compliance with regulatory frameworks and shared responsibility principles.
Periodic review processes to assess the model’s impact in high-risk applications.

---

## Bias, Risks and Limitations

Velvet has been trained on a dataset that, despite all the data curation efforts, might include toxic language and societal biases. This means that models in the Velvet family may reproduce these biases and produce harmful responses when prompted with such inputs. This is a common issue in AI models trained on large datasets, as they can inadvertently perpetuate the biases present in the data. Furthermore, the model may generate inaccurate, incomplete, or redundant responses, which could be socially unacceptable or undesirable, even if the input prompt is not explicitly offensive. This is a potential flaw in the model's design and training process, and it underscores the importance of careful validation and monitoring of AI systems to ensure that they are functioning as intended.

Additionally, using the recommended prompt template is crucial to mitigate the risk of harmful responses, as it is designed to guide the model towards more appropriate and safe outputs. However, it is important to note that the model's performance may still vary depending on the specific context and complexity of the input prompt.

Finally, when using this model in an agentic workflow, it is essential to validate that all imported packages and dependencies are from trusted sources to ensure the model's security and integrity. This is a critical step in maintaining the model's ethical and responsible use, and it is important to prioritize end-to-end security measures to prevent any potential vulnerabilities or breaches.

Future versions of Velvet will integrate automated red-teaming protocols, continuously stress-testing the model against adversarial prompts to identify and mitigate emerging risks.

## Sensitive Data Handling and Usage Restrictions

The Velvet model has not been trained on unauthorized personal data and must not be used to process sensitive data without appropriate security measures.

Usage Restrictions:

- Prohibited use on sensitive healthcare, financial, or government data without specific safeguards.
- Mandatory human validation in scenarios where the model’s outputs could have legal or ethical consequences.
- High-risk applications (legal, medical, public governance) must implement content filtering and auditing techniques to ensure response quality and safety.

## Ethical Considerations

Almawave Labs core values are openness, inclusivity, and helpfulness. We aim to create AI that is accessible and beneficial for everyone, regardless of their background. Velvet models are designed to be respectful of diverse perspectives and avoid unnecessary judgments. Therefore, Velvet models are designed to be inclusive and respectful of diverse perspectives and needs. We strive to avoid unnecessary judgment or the imposition of normative views, recognizing that content deemed problematic in some contexts can have valuable applications in others.

We deeply respect the dignity and autonomy of all users, particularly their right to free thought and expression, which are fundamental to innovation and progress.

While we have taken significant steps to ensure the safety and reliability of Velvet models, it is important to acknowledge that they may occasionally generate inaccurate, biased, or unsafe responses.

Almawave Labs is actively engaging with ethics committees and domain experts to ensure continuous oversight of Velvet’s outputs, improving safeguards through community feedback.

We strongly encourage the community to exercise caution and conduct thorough safety testing and fine-tuning when using Velvet models for specific tasks.

Opinions expressed by Velvet depend on training data and do not reflect any opinions of Almawave Labs.


## Contributions

**NLP & Knowledge Team**: Alessandro Agugliaro, Valentina Bellomaria, Simone Brozzesi, Francesco Buciuni, Cristina Giannone, Pierpaolo Goffredo, Diego Perna, Andrea Piergentili, David Preti, Matteo Radaelli, Salvatore Ricciardi, Simone Scaboro, Alfredo Serafini, Alessandro Temperoni, Beatrice Turano, Federico Wolenski, Giancarlo Xompero.

Direction: Andrea Favalli, Raniero Romagnoli.