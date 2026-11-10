FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir \
    --trusted-host pypi.org \
    --trusted-host files.pythonhosted.org \
    -r requirements.txt
COPY main.py ./
EXPOSE 5000
CMD ["gunicorn","--bind","0.0.0.0:5000","--workers","2","--timeout","30","main:app"]
