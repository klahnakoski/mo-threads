# Docker for testing

This docker image is meant for testing Linux on Windows. If you are using Linux, you do not need this.

## Instructions

All commands are meant to be run from the root directory for this repo; not this directory, rather this' grandparent.

### Build

```bash
docker build --file tests\docker\dev.dockerfile --tag mo-threads .
```

### Run

Once the docker image is built, you may run the linux tests:

```bash
docker run mo-threads
```

### Interactive

You may start the image, without running tests, and look around

```bash
docker run --interactive --tty mo-threads bash
```
