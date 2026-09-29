"""
CNN-модель для оценки нарушений (шаг 4 плана переделки). Общий backbone
(ResNet18, предобученный на ImageNet) + две небольшие "головы":
  - spine_head: 3 выхода (incorrect_positioning, axis_misaligned, artifact)
  - hip_head:   2 выхода (incorrect_positioning, incomplete_field_of_view)

Область (spine/hip) для выбора головы на инференсе берётся из эвристики
`classify_anatomical_region` (шаг 1) — независимой модели для региона нет,
и по итогам train/val сравнения (см. `model_train.py`) она не нужна: эвристика
там уже даёт 100%/86% на надёжных данных.

Backbone частично заморожен (conv1, layer1, layer2) — при ~500 снимках
(97 исследований) полное дообучение резко переобучается; тонкая настройка
только layer3/layer4 + головы плюс сильные аугментации (см. `model_data.py`
про источник и объём данных).
"""
from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn
import torchvision

from ai_module.model_data import HIP_FIELDS, SPINE_FIELDS

IMAGE_SIZE = 160
IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]


class ViolationModel(nn.Module):
    def __init__(self, pretrained: bool = True) -> None:
        # pretrained=True — только для обучения: torchvision скачивает веса
        # ImageNet из интернета. Для инференса (hybrid.py) — False: все веса
        # всё равно перезаписываются из violation_model.pt, а сеть в контейнере
        # недоступна (и ТЗ запрещает обращения к внешним сервисам).
        super().__init__()
        weights = torchvision.models.ResNet18_Weights.IMAGENET1K_V1 if pretrained else None
        backbone = torchvision.models.resnet18(weights=weights)
        self.stem = nn.Sequential(
            backbone.conv1, backbone.bn1, backbone.relu, backbone.maxpool,
            backbone.layer1, backbone.layer2,
        )
        self.trainable = nn.Sequential(backbone.layer3, backbone.layer4)
        self.pool = backbone.avgpool
        for p in self.stem.parameters():
            p.requires_grad = False
        n_features = backbone.fc.in_features  # 512
        self.spine_head = nn.Linear(n_features, len(SPINE_FIELDS))
        self.hip_head = nn.Linear(n_features, len(HIP_FIELDS))

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        with torch.no_grad():
            x = self.stem(x)
        x = self.trainable(x)
        x = self.pool(x).flatten(1)
        return self.spine_head(x), self.hip_head(x)

    def trainable_parameters(self):
        yield from self.trainable.parameters()
        yield from self.spine_head.parameters()
        yield from self.hip_head.parameters()


def crop_and_resize(img8: np.ndarray, bbox: tuple[int, int, int, int], pad_frac: float = 0.12) -> np.ndarray:
    """bbox = (y0, y1, x0, x1). Кроп главного объекта с отступом + ресайз до IMAGE_SIZE."""
    import cv2

    rows, cols = img8.shape
    y0, y1, x0, x1 = bbox
    h, w = y1 - y0 + 1, x1 - x0 + 1
    pad_y, pad_x = int(h * pad_frac), int(w * pad_frac)
    y0 = max(0, y0 - pad_y)
    y1 = min(rows - 1, y1 + pad_y)
    x0 = max(0, x0 - pad_x)
    x1 = min(cols - 1, x1 + pad_x)
    crop = img8[y0:y1 + 1, x0:x1 + 1]
    return cv2.resize(crop, (IMAGE_SIZE, IMAGE_SIZE), interpolation=cv2.INTER_LINEAR)


def to_tensor(img8_cropped: np.ndarray) -> torch.Tensor:
    """uint8 HxW (0..255) -> тензор 3xHxW, нормализован под ImageNet backbone."""
    arr = img8_cropped.astype(np.float32) / 255.0
    arr3 = np.stack([arr, arr, arr], axis=0)  # 1 канал -> 3 (реплика)
    mean = np.array(IMAGENET_MEAN, dtype=np.float32).reshape(3, 1, 1)
    std = np.array(IMAGENET_STD, dtype=np.float32).reshape(3, 1, 1)
    arr3 = (arr3 - mean) / std
    return torch.from_numpy(arr3)
