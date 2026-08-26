# Мини-образ для init-Job'а заливки корпуса на PVC (этап 10).
# Контекст сборки - КОРЕНЬ репозитория:
#   docker build -f infra/corpus-loader/Dockerfile -t vvsem/graphrag-corpus:stage10 .
FROM alpine:3.20
COPY corpus_test/ /corpus/
CMD ["sh", "-c", "cp /corpus/* /data/corpus/"]
