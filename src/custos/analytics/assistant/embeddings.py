"""Sentence-transformer modeli lazy wrapper (F8b Paket C).

Model brief v1.5 §5.1'de kilitlidir: `paraphrase-multilingual-MiniLM-L12-v2`.
İlk kullanımda HuggingFace'ten indirilir (~250 MB). Operatör internetsiz
kurulum için `HF_HOME` env var ile model cache yolunu sabitleyebilir
(`.env.example`'da yorum olarak).

Mimari not (brief §5.2): Model analytics loop'ta bir kez yüklenir ve
süreç ömrü boyunca bellekte kalır. Critical loop'a hiç dokunmaz.
Embedding çağrısı CPU'da ~50-100 ms (N100). Her çağrıda yeniden
yüklememek için modül seviyesinde singleton kullanılır.

Test edilebilirlik: Asıl model yüklemeyi ağa bağlı hale getirmemek için
`AssistantIndex` encoder'ı `callable` olarak alır. Tek büyük sınıf yerine
küçük wrapper + fonksiyon: `get_default_encoder()` prod'da singleton
döner, testler fake callable geçirir.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence

import numpy as np
import structlog

logger = structlog.get_logger(logger_name="assistant.embeddings")

# Brief §5.1'de kilitli model adı. Değiştirme = brief değişimi = versiyon artışı.
DEFAULT_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# Encoder tipi: metin listesini normalize edilmiş (L2=1) float32 ndarray'e
# dönüştüren bir callable. Shape: (n, embedding_dim).
Encoder = Callable[[Sequence[str]], np.ndarray]


class _EmbeddingSingleton:
    """Sentence-transformer modelini thread-safe lazy yüklenen wrapper.

    Modeli ilk `encode` çağrısında yükler; sonraki çağrılar aynı örneği
    yeniden kullanır. `SentenceTransformer.encode` zaten batch destekli
    ve thread-safe'dir (PyTorch inference mode).
    """

    def __init__(self, model_name: str = DEFAULT_MODEL_NAME) -> None:
        self._model_name = model_name
        self._model: object | None = None
        self._lock = threading.Lock()

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        """Metin listesini embedding ndarray'e çevirir (L2 normalize edilmiş)."""
        if not texts:
            # Boş girdi — embedding_dim'i bilmeden boş ndarray döndür.
            # Arama indeksi build zamanında bunu yakalar ve indeksi boş kurar.
            return np.zeros((0, 0), dtype=np.float32)

        model = self._ensure_loaded()
        # `normalize_embeddings=True` → cosine = inner product (faiss IndexFlatIP).
        # `convert_to_numpy=True` → torch.Tensor yerine ndarray döner.
        embeddings = model.encode(  # type: ignore[attr-defined]
            list(texts),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        arr: np.ndarray = np.asarray(embeddings, dtype=np.float32)
        return arr

    def _ensure_loaded(self) -> object:
        """İlk çağrıda modeli diskten/HF'den yükler."""
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            logger.info("loading_embedding_model", model=self._model_name)
            # Import burada: modül yüklemesi test ortamında pahalı olmasın.
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
            logger.info("embedding_model_loaded", model=self._model_name)
            return self._model


# Modül seviyesinde tek örnek. `get_default_encoder()` bunu kullanır.
_DEFAULT = _EmbeddingSingleton()


def get_default_encoder() -> Encoder:
    """Varsayılan (brief'te kilitli) multilingual modelin encoder callable'ını döner.

    Üretim yolunda indeks bu callable ile kurulur. Testler kendi fake
    encoder'larını geçebilir (ağa çıkmadan, hızlı)."""
    return _DEFAULT.encode
