{{/*
Имя релиза/чарта и общие лейблы. MVP-минимум.
*/}}
{{- define "graphrag.labels" -}}
app.kubernetes.io/part-of: graphrag
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "graphrag.failNoSecrets" -}}
{{- if not .Values.secrets -}}
{{- fail "Секреты не заданы: передайте --values secrets.yaml (шаблон secrets.yaml.example)" -}}
{{- end -}}
{{- range $k := list "postgresPassword" "neo4jPassword" "jwtSecret" "langfusePublicKey" "langfuseSecretKey" "vaultRootToken" -}}
{{- if not (index $.Values.secrets $k) -}}
{{- fail (printf "secrets.%s пуст - заполни secrets.yaml" $k) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Контрольная сумма секретов: пересоздание seed-Job'а при изменении значений.
*/}}
{{- define "graphrag.secretsChecksum" -}}
{{- toYaml .Values.secrets | sha256sum -}}
{{- end -}}
