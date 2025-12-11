# Использование базового образа Python
FROM python:3.10-slim

# Установка системных зависимостей, необходимых для aiohttp/компиляции
RUN apt-get update && \
    apt-get install -y build-essential

# Установка рабочей директории
WORKDIR /app

# Копирование и установка зависимостей Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование остального кода
COPY . .

# Команда запуска (которая заменит Procfile)
CMD ["python", "bot.py"]
