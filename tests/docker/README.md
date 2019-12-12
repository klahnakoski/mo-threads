# Docker for testing

This docker image is meant for testing Linux on Windows. If you are using Linux, you do not need this.

## Instructions

All commands are meant to be run from the root directory for this repo; not this directory, rather this' grandparent.

### Build and Run

To run the tests, build and run:


```bash
docker build --file tests\docker\dev.dockerfile --tag mo-threads .
docker run mo-threads
```

### Interactive

Instead of running may start the image, without running tests:

```bash
docker run --interactive --tty mo-threads bash
```
