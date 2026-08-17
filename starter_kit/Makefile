.PHONY: verify unit l2

verify:
	docker build -t loomq-submission .
	docker run --rm loomq-submission

unit:
	python3 -m unittest discover -s tests -v

l2:
	python3 evaluator.py --level l2
