"""Teknik asistan chatbot modülü (F8b).

Kapsam (brief v1.5 §4.9 + v1.7 §4.9):
- LLM kullanılmaz. Cevaplar yalnızca `data/knowledge/` altındaki
  Markdown ve YAML dokümanlardan gelir.
- Semantic search (sentence-transformers + FAISS) yalnızca doğru
  dokümanı bulmak için kullanılır; cevap üretmez.
- Custos veri entegrasyonu (asset/alarm/bakım) v1 kapsam dışı —
  pilot sonrası v1.1 backlog.

Bileşenler (paket paket doldurulur):
- `loader`: Markdown + YAML → Chunk listesi
- `embeddings`: sentence-transformers modeli wrapper
- `index`: FAISS tabanlı vektör indeksi
- `retriever`: YAML exact-match + semantic search orchestrator
"""

from __future__ import annotations
