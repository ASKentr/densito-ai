"""
Финальное обучение CNN на всех доступных данных (без hold-out) — для полей,
где кросс-валидация (`model_train.py`) показала преимущество модели над
эвристикой. Сохраняет веса в `weights/violation_model.pt` для `hybrid.py`.
"""
from __future__ import annotations

import random
import warnings

import torch

from ai_module.model import ViolationModel
from ai_module.model_data import build_examples
from ai_module.model_train import EPOCHS, SEED, ExampleDataset, compute_pos_weight, masked_bce
from ai_module.model_data import HIP_FIELDS, SPINE_FIELDS
from torch.utils.data import DataLoader

warnings.filterwarnings("ignore")


def train_final(xlsx: str, studies_dir: str, out_path: str) -> None:
    examples = build_examples(xlsx, studies_dir)
    model = ViolationModel()
    model.train()
    optimizer = torch.optim.Adam(model.trainable_parameters(), lr=1e-4)

    spine_pos_weight = compute_pos_weight(examples, "spine", SPINE_FIELDS)
    hip_pos_weight = compute_pos_weight(examples, "hip", HIP_FIELDS)
    loader = DataLoader(ExampleDataset(examples, augment=True), batch_size=16, shuffle=True)

    for epoch in range(EPOCHS):
        total_loss = 0.0
        for tensor, spine_t, spine_m, hip_t, hip_m, _ in loader:
            optimizer.zero_grad()
            spine_logits, hip_logits = model(tensor)
            loss = (
                masked_bce(spine_logits, spine_t, spine_m, spine_pos_weight)
                + masked_bce(hip_logits, hip_t, hip_m, hip_pos_weight)
            )
            loss.backward()
            optimizer.step()
            total_loss += float(loss)
        print(f"epoch {epoch}: loss={total_loss / len(loader):.4f}", flush=True)

    torch.save(model.state_dict(), out_path)
    print(f"saved to {out_path}", flush=True)


if __name__ == "__main__":
    import sys

    torch.manual_seed(SEED)
    random.seed(SEED)
    xlsx = sys.argv[1]
    studies_dir = sys.argv[2]
    out_path = sys.argv[3] if len(sys.argv) > 3 else "ai_module/weights/violation_model.pt"
    train_final(xlsx, studies_dir, out_path)
