"""
app/services/recommendation.py — генерация рекомендаций по риску.

Функция generate_recommendations(risk_class, snapshot) → list[str].

Логика:
1. Базовые рекомендации по классу (0/1/2).
2. Модификаторы: ЖНВЛП, наркотик, аналоги, число производителей/стран, возраст.
3. Дедупликация и сортировка по приоритету.
4. Не более MAX_RECOMMENDATIONS (6) — чтобы UI не перегружался.

⚠️ Рекомендации НЕ являются медицинскими/юридическими указаниями.
   Это эвристики для логистики/закупок на основе признаков СМНН.
"""

from __future__ import annotations

from app.schemas.response import FeaturesSnapshot


# Максимум рекомендаций в ответе (UI-дружелюбно)
MAX_RECOMMENDATIONS = 6

# Пороги (можно вынести в config, но для MVP — константы)
MIN_PRODUCERS_FOR_STABILITY = 3     # меньше — уязвимость к сбоям
MAX_AGE_BEFORE_CHECK = 15           # лет; старше — проверить регистрацию
MIN_COUNTRIES_FOR_STABILITY = 2     # меньше — геополитический риск


# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================

def generate_recommendations(
    risk_class: int,
    snapshot: FeaturesSnapshot,
) -> list[str]:
    """
    Генерирует рекомендации по риску.

    Параметры:
        risk_class — 0 (низкий) / 1 (средний) / 2 (высокий).
        snapshot   — features_snapshot из RiskResponse.

    Возвращает:
        list[str] — рекомендации, приоритизированные (критичные первыми),
                    без дублей, не более MAX_RECOMMENDATIONS.
    """
    # Собираем с приоритетами: (priority, текст)
    # priority: 1 = критично, 2 = срочно, 3 = планово, 4 = поддерживающе
    recs: list[tuple[int, str]] = []

    # --- 1. Базовые по классу ---
    recs.extend(_base_by_class(risk_class))

    # --- 2. Модификаторы ---
    recs.extend(_modifiers(snapshot, risk_class))

    # --- 3. Дедупликация (по тексту, сохраняя min(priority)) ---
    seen: dict[str, int] = {}
    for prio, text in recs:
        if text not in seen or prio < seen[text]:
            seen[text] = prio

    # --- 4. Сортировка: сначала priority (1,2,3,4), потом — порядок добавления ---
    ordered = sorted(seen.items(), key=lambda kv: kv[1])

    # --- 5. Ограничение ---
    result = [text for text, _ in ordered[:MAX_RECOMMENDATIONS]]
    return result


# ============================================================
# БАЗОВЫЕ ПО КЛАССУ
# ============================================================

def _base_by_class(risk_class: int) -> list[tuple[int, str]]:
    if risk_class == 2:  # высокий
        return [
            (2, "Увеличить страховой запас (рекомендуется ×1.5 от текущего)."),
            (2, "Связаться с альтернативными поставщиками в течение 48 часов."),
            (3, "Пересмотреть нормативы хранения и логистики для этого СМНН."),
        ]
    if risk_class == 1:  # средний
        return [
            (3, "Мониторить остатки еженедельно."),
            (3, "Пересмотреть нормативы страхового запаса."),
        ]
    # низкий
    return [
        (4, "Поддерживать текущий уровень запаса."),
        (4, "Плановый пересмотр — через квартал."),
    ]


# ============================================================
# МОДИФИКАТОРЫ (по флагам snapshot)
# ============================================================

def _modifiers(snapshot: FeaturesSnapshot, risk_class: int) -> list[tuple[int, str]]:
    mods: list[tuple[int, str]] = []

    # --- ЖНВЛП: всегда приоритетно ---
    if snapshot.is_essential:
        if risk_class == 2:
            mods.append((1, "⚠️ ЖНВЛП: эскалировать в отдел закупок немедленно."))
        elif risk_class == 1:
            mods.append((2, "ЖНВЛП: приоритетный контроль (еженедельно)."))
        else:
            mods.append((4, "ЖНВЛП: держать на контроле в плановом режиме."))

    # --- Наркотик: особые правила оборота ---
    if snapshot.is_narcotic:
        mods.append(
            (1, "Наркотический/психотропный: проверить лицензию и строгий учёт.")
        )

    # --- Есть аналоги по ATC ---
    if snapshot.has_atc_substitutes and risk_class >= 1:
        mods.append((3, "Рассмотреть аналоги по ATC-группе как резерв."))

    # --- Нет аналогов + риск высокий → критично ---
    if not snapshot.has_atc_substitutes and risk_class == 2:
        mods.append(
            (1, "Нет аналогов по ATC: рассматривать как критичный препарат.")
        )

    # --- Мало производителей ---
    if snapshot.producers_count < MIN_PRODUCERS_FOR_STABILITY and risk_class >= 1:
        mods.append(
            (2, f"Мало производителей ({snapshot.producers_count}): расширить пул поставщиков.")
        )

    # --- Старый препарат ---
    if snapshot.avg_age > MAX_AGE_BEFORE_CHECK:
        mods.append(
            (3, f"Препарат давно на рынке (средний возраст {snapshot.avg_age:.1f} лет): проверить актуальность регистрации.")
        )

    # --- Нет данных о странах в snapshot — пропускаем, но если бы было: ---
    # if snapshot.countries_count < MIN_COUNTRIES_FOR_STABILITY: ...

    return mods