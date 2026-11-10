FROM python:3.12-slim
WORKDIR /srv/kb
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0
COPY app ./app
COPY static ./static
COPY deploy/poc ./deploy/poc
COPY deploy/create_admin.py ./deploy/create_admin.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "300", "--access-logfile", "-", "--error-logfile", "-", "app.wsgi:application"]
