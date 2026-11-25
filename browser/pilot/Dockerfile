ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}
USER root
RUN mkdir /state && chown 10001:10001 /state
COPY *.py extractor.js /pilot/
USER 10001:10001
CMD ["python", "/pilot/launch.py"]
