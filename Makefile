.PHONY: install run test sweep lint

install:
	pip install -r requirements.txt -r requirements-dev.txt

run:
	uvicorn src.proxy.main:app --reload --host 127.0.0.1 --port 8000

test:
	pytest tests/ -v

sweep:
	curl -X POST http://127.0.0.1:8000/eval/threshold-sweep \
		-H "Content-Type: application/json" \
		-d "{\"thresholds\": [0.80, 0.82, 0.85, 0.88, 0.90, 0.93, 0.95]}"

lint:
	ruff check src/ tests/
