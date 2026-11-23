.PHONY: pipeline gtfs load speed api web test

pipeline:
	python scripts/run_pipeline.py all

gtfs:
	python scripts/run_pipeline.py gtfs

load:
	python scripts/run_pipeline.py load

speed:
	python scripts/run_pipeline.py speed

api:
	uvicorn src.api.main:app --reload --port 8000

web:
	python -m http.server 5500 --directory frontend

test:
	pytest -q
