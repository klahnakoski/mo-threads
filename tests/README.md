# Development

## Setup

For an ec2 machine, you may need to install some prerquisists

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

Install requirements

    python -m pip install -r requirements.txt
    python -m pip install -r tests/requirements.txt


## Run tests

    python -u -m unittest discover -v tests