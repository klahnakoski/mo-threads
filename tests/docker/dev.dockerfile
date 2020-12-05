FROM python:3.7.2

WORKDIR /app
RUN mkdir tests
COPY requirements.txt /app
COPY tests/requirements.txt /app/tests
RUN pip install -r tests/requirements.txt \
    && pip install .

ADD . /app
CMD python -m unittest discover tests
