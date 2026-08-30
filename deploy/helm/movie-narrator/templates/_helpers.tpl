{{/* SPDX-FileCopyrightText: 2026 zcbacxc */}}
{{/* SPDX-License-Identifier: AGPL-3.0-or-later */}}

{{/* Expand the name of the chart. */}}
{{- define "movie-narrator.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Create a default fully qualified app name (max 63 chars, DNS-1123). */}}
{{- define "movie-narrator.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name (default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-") | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/* Chart name and version as used by the chart label. */}}
{{- define "movie-narrator.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end }}

{{/* Common labels. */}}
{{- define "movie-narrator.labels" -}}
helm.sh/chart: {{ include "movie-narrator.chart" . }}
{{ include "movie-narrator.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* Selector labels. */}}
{{- define "movie-narrator.selectorLabels" -}}
app.kubernetes.io/name: {{ include "movie-narrator.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Service account name. */}}
{{- define "movie-narrator.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "movie-narrator.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/* Name of the Secret holding MN_API_KEY (created or pre-existing). */}}
{{- define "movie-narrator.secretName" -}}
{{- if .Values.auth.existingSecret }}
{{- .Values.auth.existingSecret }}
{{- else }}
{{- include "movie-narrator.fullname" . }}
{{- end }}
{{- end }}

{{/* Name of the ConfigMap holding job.yaml (created or pre-existing). */}}
{{- define "movie-narrator.configName" -}}
{{- if .Values.config.existingConfigmap }}
{{- .Values.config.existingConfigmap }}
{{- else }}
{{- include "movie-narrator.fullname" . }}
{{- end }}
{{- end }}
