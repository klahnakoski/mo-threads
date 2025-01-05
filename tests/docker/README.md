# Docker for testing

This docker image is meant for testing Linux on Windows. If you are using Linux, you do not need this.

## Instructions

All commands are meant to be run from the root directory for this repo; not this directory, rather this' grandparent.

### Build

To run the tests, build:

```bash
docker build --file tests\docker\python310.dockerfile --tag mo-threads .
```

### Interactive

Instead of running may start the image, without running tests:

```bash
python310.dockerfile run -it mo-threads bash
```

Then run tests

    .venv/bin/python -m unittest discover . -v

 .venv/bin/python -m unittest tests.test_processes.TestProcesses.test_sigint_no_exit