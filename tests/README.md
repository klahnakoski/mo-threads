# Development


## Docker Testing

This docker image is meant for testing Linux on Windows. If you are using Linux, you do not need this.

## Instructions

All commands are meant to be run from the root directory for this repo; not this directory, rather this' parent.

#### Build and Run

To run the tests, build and run:


```bash
python310.dockerfile build --file tests\docker\dev.dockerfile --tag mo-threads .
python310.dockerfile run mo-threads
```

#### Interactive

Instead of running all tests, you may start the image without running tests:

```bash
python310.dockerfile run --interactive --tty mo-threads bash
```



## EC2 Testing

### Setup

If you want to run testing on For an ec2 machine, you must install some perquisites

    # INSTALL GIT
    sudo yum install -y git-core

    # GET PIP
    rm -fr /home/ec2-user/temp
    mkdir  /home/ec2-user/temp
    cd /home/ec2-user/temp
    wget https://bootstrap.pypa.io/get-pip.py
    sudo python get-pip.py
    sudo ln -s /usr/local/bin/pip /usr/bin/pip
    
    # INSTALL GCC
    sudo yum install -y libffi-devel
    sudo yum install -y openssl-devel
    sudo yum groupinstall -y "Development tools"

### Install 

    git clone https://github.com/klahnakoski/mo-threads.git
    cd mo-threads
    python -m pip install -r requirements.txt
    python -m pip install -r tests/requirements.txt

### Run Tests

    python -u -m unittest discover -v tests