"""Contrato do texto e validação dos vetores (R0, K6), sem banco e sem baixar modelo."""

import os

import numpy as np
import pytest

from nvvs.embed import DIM, MODEL_NAME, check_vectors, embedding_text


def unit(n: int, seed: int = 0) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=(n, DIM)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_text_contract_v1_is_the_title_with_collapsed_whitespace():
    assert embedding_text("  TinyRAG:\n  Retrieval\tfor   Small Models ") == (
        "TinyRAG: Retrieval for Small Models"
    )


def test_text_contract_v1_has_no_prefix():
    assert not embedding_text("Rust 1.99").startswith(("query:", "passage:"))


def test_model_is_the_one_chosen_on_08_11():
    assert MODEL_NAME == "BAAI/bge-small-en-v1.5"
    assert DIM == 384


def test_accepts_normalized_vectors():
    check_vectors(unit(3), 3)


@pytest.mark.parametrize(
    "vectors, message",
    [
        (unit(3)[:, :100], "formato"),  # dimensão errada
        (unit(2), "formato"),  # quantidade errada para n=3
        (unit(3) * 2, "normalizados"),
        (np.full((3, DIM), np.nan, dtype=np.float32), "NaN"),
    ],
)
def test_rejects_vectors_that_break_the_contract(vectors, message):
    with pytest.raises(ValueError, match=message):
        check_vectors(vectors, 3)


@pytest.mark.skipif(
    os.environ.get("NVVS_MODEL_TESTS") != "1",
    reason="baixa e roda o modelo real: defina NVVS_MODEL_TESTS=1",
)
def test_real_model_meets_the_contract_and_ranks_sensibly():
    from nvvs.embed import model_encoder

    encode = model_encoder()
    v = encode(["Rust 1.99 released", "Rust programming language", "Carrot cake recipe"])
    check_vectors(v, 3)
    assert v[0] @ v[1] > v[0] @ v[2]
