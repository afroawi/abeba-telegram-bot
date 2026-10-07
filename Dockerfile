FROM python:3.13-slim
WORKDIR /app
COPY requirements.lock.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY src ./src
RUN pip install --no-cache-dir --no-deps . && useradd --create-home abeba
USER abeba
CMD ["python", "-m", "abeba_bot"]
