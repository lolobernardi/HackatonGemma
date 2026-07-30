from app.main import _modelo_disponible


def test_modelo_disponible_exige_tag_exacto():
    disponibles = ["gemma4:e2b-it-qat", "otro:latest"]
    assert _modelo_disponible("gemma4:e2b-it-qat", disponibles)
    assert not _modelo_disponible("gemma4:e4b", disponibles)
