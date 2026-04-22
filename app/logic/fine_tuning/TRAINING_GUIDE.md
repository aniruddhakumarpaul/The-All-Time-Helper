# 🚀 Google Colab Fine-Tuning Guide (Multifaceted Persona)

Follow these steps to train your custom **Funny, Intellectual, Concerned, Cute** persona using the dataset we generated.

## Phase 1: Preparation
1. Open [Google Colab](https://colab.research.google.com/).
2. Create a new notebook and set the Runtime to **GPU** (T4, A100, or L4).
3. Upload your `personality_dataset.jsonl` file to the Colab session storage.

## Phase 2: The Training Code
Copy and paste this code into a single cell and run it:

```python
# 1. Install Unsloth & Requirements (Latest 2026 Version)
!pip install --upgrade --no-cache-dir "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git"
!pip install --upgrade --no-deps "git+https://github.com/unslothai/unsloth_zoo.git"
!pip install --no-deps xformers trl peft accelerate bitsandbytes

# 2. Load the Model (Llama 3.2 3B)
from unsloth import FastLanguageModel
import torch
max_seq_length = 2048
dtype = None # Auto detected
load_in_4bit = True # Use 4bit to save VRAM

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name = "unsloth/Llama-3.2-3B-Instruct",
    max_seq_length = max_seq_length,
    dtype = dtype,
    load_in_4bit = load_in_4bit,
)

# 3. Apply LoRA Adapters
model = FastLanguageModel.get_peft_model(
    model,
    r = 16, # Rank
    target_modules = ["q_proj", "k_proj", "v_proj", "o_proj",
                      "gate_proj", "up_proj", "down_proj",],
    lora_alpha = 16,
    lora_dropout = 0,
    bias = "none",
    use_gradient_checkpointing = "unsloth", 
    random_state = 3407,
    use_rslora = False,
    loftq_config = None,
)

# 4. Load the Dataset
from datasets import load_dataset
dataset = load_dataset("json", data_files="personality_dataset.jsonl", split="train")

def formatting_prompts_func(examples):
    instructions = examples["instruction"]
    outputs      = examples["output"]
    texts = []
    for instruction, output in zip(instructions, outputs):
        text = f"### Instruction:\n{instruction}\n\n### Response:\n{output}"
        texts.append(text)
    return { "text" : texts, }

dataset = dataset.map(formatting_prompts_func, batched = True,)

# 5. Training
from trl import SFTTrainer
from transformers import TrainingArguments

trainer = SFTTrainer(
    model = model,
    tokenizer = tokenizer,
    train_dataset = dataset,
    dataset_text_field = "text",
    max_seq_length = max_seq_length,
    dataset_num_proc = 2,
    packing = False, 
    args = TrainingArguments(
        per_device_train_batch_size = 2,
        gradient_accumulation_steps = 4,
        warmup_steps = 5,
        max_steps = 60, # Fast run for 100 samples
        learning_rate = 2e-4,
        fp16 = not torch.cuda.is_bf16_supported(),
        bf16 = torch.cuda.is_bf16_supported(),
        logging_steps = 1,
        optim = "adamw_8bit",
        weight_decay = 0.01,
        lr_scheduler_type = "linear",
        seed = 3407,
        output_dir = "outputs",
    ),
)
trainer.train()

# 6. Export to GGUF (For Ollama)
model.save_pretrained_gguf("model", tokenizer, quantization_method = "q4_k_m")
```

## Phase 3: Integration
1. Run the code above. It will generate a folder named `model` containing `unsloth.Q4_K_M.gguf`.
2. Download that `.gguf` file to your local machine.
3. Place it in the `The_All_Time_helper/app/logic/fine_tuning/` directory.
4. Run the following command in your terminal to create the Ollama model:
   ```bash
   ollama create persona-helper -f Modelfile
   ```

*(I will provide the Modelfile and the agents.py hook once you have the GGUF)*.
