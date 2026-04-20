FROM python:3.12-slim
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /claude-code-proxy

COPY pyproject.toml uv.lock ./

RUN pip install --no-cache-dir --upgrade uv \
 && uv sync

COPY . .

EXPOSE 8082

CMD ["uv", "run", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8082"]