# Результаты проверок (реальный вывод)

Прогон 2026-10-29 после выноса `app/dependencies.py` и
`app/services/unit_of_work.py`, переименования миграции и удаления старых
точек входа. Windows 11, Python 3.12.3, PostgreSQL 17 в Docker Compose.
Каждая проба создаёт свою временную БД; рабочая БД не менялась (отпечаток
до/после совпал, см. `regression.txt`).

| Файл | Что это | Итог |
|---|---|---|
| `regression.txt` | сводка 31 пробы M1–M5 | `REGRESSION PASSED`, 0 ошибок |
| `m2_contract_review.txt` | ревью контракта M2 (чтение каталога, фильтры, 404/409/422) | `ALL CHECKS PASSED`, 87/0 |
| `m3_review_probe.txt` | ревью M3 (доступ, JWT, роли) | `ALL CHECKS PASSED`, 155/0 |
| `m4_review_probe.txt` | ревью M4 (корзина) | `ALL M4 REVIEW CHECKS PASSED`, 155/0 |
| `m5_review_probe.txt` | ревью M5 (checkout, история, статусы) | `ALL M5 REVIEW CHECKS PASSED`, 31/0 |
| `error_contract.txt` | контракт ошибок 401/403/404/409/422 | `ALL ERROR CONTRACT CHECKS PASSED` |

Скрипты проб (`_*_probe.py`, `_m2_contract_review.py` и др.) — локальные
вспомогательные файлы: они в `.gitignore` и в комплект не входят. Токены и
пароли в выводе не печатаются (проверено поиском).

Не проверялось: Python 3.13, macOS/Linux, первый старт Docker на новом
компьютере.
