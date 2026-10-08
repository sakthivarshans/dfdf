from engine.paddle_engine import PaddleEngine


def test_engine():

    engine = PaddleEngine()

    results = engine.predict_image(
        "storage/pages/sample/page_006.png"
    )

    assert results is not None