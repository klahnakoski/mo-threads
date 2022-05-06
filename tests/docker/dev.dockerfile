FROM python:3.7.2

WORKDIR /app
RUN mkdir tests
COPY ./ /app/
RUN pip install .
#RUN python tests/smoke_test.py
#RUN pip install tests/requirements.txt

#CMD python -m unittest discover tests
