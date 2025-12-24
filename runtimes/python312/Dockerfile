FROM python:3.12-slim

WORKDIR /action

COPY actionproxy.py .

EXPOSE 8080

USER nobody

CMD ["python", "-u", "actionproxy.py"]
