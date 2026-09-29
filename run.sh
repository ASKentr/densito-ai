#!/usr/bin/env bash
# Densito-AI — сборка и запуск контейнеризированного решения (Linux / UNIX).
#
#   ./run.sh up                         собрать образы и запустить сервис (веб + API)
#   ./run.sh batch <вход> <результат>   пакетная обработка: папка, zip или DICOM-файл
#                                       -> таблица .xlsx или .csv (ТЗ п.2.5);
#                                       с флагом --sr ещё и отчёты DICOM SR в папку sr/ рядом
#                                       с таблицей (ТЗ п.2.6)
#   ./run.sh build                      только собрать образы
#   ./run.sh down                       остановить сервис (данные сохраняются)
#   ./run.sh status | logs              состояние / журналы контейнеров
#   ./run.sh reset                      остановить и УДАЛИТЬ данные (БД, загруженные файлы)
#
# Требуется Docker Engine с плагином compose (docker compose) или docker-compose.
# При первом запуске создаётся .env со случайным SECRET_KEY; адрес, по которому
# открывают веб-интерфейс, задаётся переменной PUBLIC_HOST (по умолчанию
# localhost), например:  PUBLIC_HOST=10.0.0.5 ./run.sh up
# Подробно — README.md и docs/РАЗВЁРТЫВАНИЕ.md.
set -euo pipefail

cd "$(dirname "$0")"

die() { echo "ошибка: $*" >&2; exit 1; }

if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
else
    die "не найден Docker Compose. Установите Docker Engine и плагин compose: https://docs.docker.com/engine/install/"
fi

ensure_env() {
    [[ -f .env ]] && return
    local host="${PUBLIC_HOST:-localhost}" secret
    if command -v openssl >/dev/null 2>&1; then
        secret="$(openssl rand -hex 32)"
    else
        secret="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    fi
    cat > .env <<EOF
# Создано ./run.sh $(date '+%Y-%m-%d %H:%M'). Не публикуйте этот файл.
SECRET_KEY=${secret}
# Демо-учётки создаются при первом старте (пустая БД). Для стенда, доступного
# извне, смените пароли ДО первого запуска (или выполните ./run.sh reset).
BOOTSTRAP_ADMIN_PASSWORD=admin12345
BOOTSTRAP_EXPERT_PASSWORD=expert12345
BACKEND_PORT=8000
FRONTEND_PORT=5173
# Адрес backend так, как его видит браузер пользователя (вшивается в frontend при сборке)
API_PUBLIC_URL=http://${host}:8000
CORS_ORIGINS=["http://${host}:5173","http://localhost:5173","http://127.0.0.1:5173"]
EOF
    chmod 600 .env
    echo "создан .env (SECRET_KEY сгенерирован, адрес веб-интерфейса: http://${host}:5173)"
}

# значение переменной из .env без source (там JSON с кавычками)
env_value() { grep -E "^$1=" .env | tail -n 1 | cut -d= -f2-; }

cmd="${1:-}"
case "$cmd" in
    up)
        ensure_env
        "${COMPOSE[@]}" up -d --build
        api_url="$(env_value API_PUBLIC_URL)"
        echo
        echo "Densito-AI запущен:"
        echo "  веб-интерфейс:     ${api_url%:*}:$(env_value FRONTEND_PORT)"
        echo "  API и Swagger:     ${api_url}/docs"
        echo "  демо-учётки:       admin / expert (пароли — в .env)"
        ;;
    build)
        ensure_env
        "${COMPOSE[@]}" build
        ;;
    batch)
        sr_args=()
        if [[ $# -eq 4 && "$4" == "--sr" ]]; then
            sr_args=(--sr /output/sr); set -- "$1" "$2" "$3"
        fi
        [[ $# -eq 3 ]] || die "использование: ./run.sh batch <папка|архив.zip|файл.dcm> <результат.xlsx|.csv> [--sr]"
        in_path="$2"; out_path="$3"
        [[ -e "$in_path" ]] || die "нет такого файла или папки: $in_path"
        case "$out_path" in *.xlsx|*.csv) ;; *) die "результат должен быть .xlsx или .csv" ;; esac
        ensure_env
        in_abs="$(cd "$(dirname "$in_path")" && pwd)/$(basename "$in_path")"
        out_dir="$(dirname "$out_path")"
        mkdir -p "$out_dir"
        out_dir_abs="$(cd "$out_dir" && pwd)"
        out_name="$(basename "$out_path")"
        if [[ -d "$in_abs" ]]; then
            mount=(-v "${in_abs}:/input:ro"); target="/input"
        else
            mount=(-v "$(dirname "$in_abs"):/input:ro"); target="/input/$(basename "$in_abs")"
        fi
        "${COMPOSE[@]}" run --rm --no-deps --user "$(id -u):$(id -g)" \
            "${mount[@]}" -v "${out_dir_abs}:/output" \
            backend python batch_predict.py "$target" "/output/${out_name}" ${sr_args[@]+"${sr_args[@]}"}
        ;;
    down)
        "${COMPOSE[@]}" down
        ;;
    status)
        "${COMPOSE[@]}" ps
        ;;
    logs)
        "${COMPOSE[@]}" logs --tail=200 -f
        ;;
    reset)
        read -r -p "Удалить все данные сервиса (БД, загруженные исследования)? [y/N] " ans
        [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "отменено"; exit 0; }
        "${COMPOSE[@]}" down -v
        ;;
    *)
        sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'
        [[ -z "$cmd" ]] || exit 1
        ;;
esac
