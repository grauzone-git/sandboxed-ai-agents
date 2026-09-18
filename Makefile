.PHONY: test check

test:
	./tests/run

check:
	python3 -B tests/check-sources.py
