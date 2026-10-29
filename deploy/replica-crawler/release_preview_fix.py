"""Release the same-origin image preview fix from the integrated production base."""

import argparse
from pathlib import Path

import release as runner

runner.ROOT = Path("/srv/huipin-product-preview-20261029")
runner.BASE_BACKEND = "huipin-integrated/backend:20261029-everyinfra"
runner.BASE_FRONTEND = "huipin-integrated/frontend:20261029-everyinfra"
runner.BACKEND = "huipin-integrated/backend:20261029-preview"
runner.FRONTEND = "huipin-integrated/frontend:20261029-preview"
runner.EXTRA_TESTS = ("tests/test_product_previews.py",)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "test", "activate"])
    getattr(runner, parser.parse_args().action)()
