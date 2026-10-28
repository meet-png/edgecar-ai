"""Fine-tuning the command model: training data, LoRA training, export to GGUF.

Training runs offline on a workstation GPU (``pip install -e .[train]``); the vehicle only ever runs the exported
GGUF through llama.cpp, exactly like the few-shot model.
"""
