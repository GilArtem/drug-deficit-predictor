"""
- app/core/feature_builder.py

feature_builder.py — расчёт 74 признаков для honest-модели XGB-honest на лету.

Вход:  smnn_code
Выход: DataFrame 1×74 с признаками в порядке metadata_v1.json['features']
       минус PROXIES (current_stock_days, stock_to_sales_ratio)

Эталон: synthetic_data.drug_features

ВАЖНО (из Анализ_и_предобработка_данных.ipynb):
- producers_count считается по name_producer_normalized (справочник producer_canon)
- country_group_mode — по country_group (справочник country_groups)
- primary_unit_name — из unit_canon (канонические имена, не сырые)
- atc_level4_mode = LEFT(code_atc_clean, 5) — 5 символов, 'Unknown' → 'Unkno'
- atc_substitutes_count — по LEFT(code_atc, 5), а не по первой букве
- atc_is_unknown = (atc_level4_mode == 'Unkno')
- has_russian_producer = MAX(is_russian), а не (country == 'РОССИЯ')
"""

from __future__ import annotations
import json
import logging
from typing import Optional

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# ============================================================
# КОНСТАНТЫ
# ============================================================

#  список признаков, которые детерминированно определяют target
PROXIES = ['current_stock_days', 'stock_to_sales_ratio'] 

# 4 категориальные колонки, которые нужно закодировать через OneHotEncoder
OHE_COLS = [
    'atc_group_mode',
    'country_group_mode',
    'primary_unit_type',
    'primary_unit_name',
]

# защита от ошибки.  Если metadata_v1.json изменится и даст не 74, а 75 фич — программа упадёт с понятной ошибкой, а не выдаст молча мусор.
EXPECTED_N_FEATURES_HONEST = 74


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (те же, что в ноутбуке)
# ============================================================

def extract_first_valid_atc(s) -> Optional[str]:
    """Извлекает первый валидный ATC-код из строки (как в ноутбуке)."""
    if pd.isna(s):
        return None
    for part in str(s).split(';'):
        part = part.strip()
        if part and part != '~' and len(part) >= 1 and part[0].isalpha():
            return part
    return None


def normalize_atc_level4(s) -> Optional[str]:
    """Приводит ATC-код к 5 символам (уровень 4)."""
    if pd.isna(s):
        return None
    s = str(s).strip().upper()
    return s[:5] if len(s) >= 5 else s


def safe_mode(series: pd.Series):
    """Безопасная мода: если все NaN — возвращает None."""
    s = series.dropna()
    if len(s) == 0:
        return None
    mode_vals = s.mode()
    return mode_vals.iloc[0] if len(mode_vals) > 0 else None


# ============================================================
# КЛАСС
# ============================================================

class FeatureBuilder:
    def __init__(self, db_engine: Engine, ohe_path: str, metadata_path: str):

        # сохраняем подключение к PostgreSQL (SQLAlchemy Engine).
        self.engine = db_engine

        # --- OneHotEncoder ---
        # загружаем обученный OneHotEncoder из onehot_encoder_v1.pkl. Он «помнит», какие категории видел при обучении 
        # (15 ATC-групп, 7 групп стран, 6 типов единиц, 14 названий единиц).
        # ⚠️ Почему критично: если создать новый OneHotEncoder, он не будет знать категорий → 
        # создаст другие колонки → модель получит мусор. Надо использовать тот же encoder, что при обучении.
        self.ohe = joblib.load(ohe_path)
        logger.info(f"OneHotEncoder: {[len(c) for c in self.ohe.categories_]}")

        # --- metadata ---
        # загружаем metadata_v1.json, где лежит список всех 76 фич в правильном порядке.
        with open(metadata_path, 'r', encoding='utf-8') as f:
            self.metadata = json.load(f)

        full_features = self.metadata['features']
        # вычитаем 2 прокси → получаем 74 фичи honest.
        self.honest_features = [f for f in full_features if f not in PROXIES]

        # защита. Если получилось не 74 — падаем с ошибкой.
        assert len(self.honest_features) == EXPECTED_N_FEATURES_HONEST, \
            f"Ожидалось {EXPECTED_N_FEATURES_HONEST} фич, получено {len(self.honest_features)}"

        # --- Кеш ATC-групп (по 5-символьному коду!) ---
        # ВАЖНО: в ноутбуке atc_level4_mode = atc_clean.str[:5]
        # Значит, кеш строится по LEFT(code_atc, 5), а не по первой букве.
        # Один SQL-запрос по всей таблице при старте. Зачем кеш: atc_substitutes_count для каждого СМНН требует 
        # общего числа СМНН в той же ATC-группе. Если считать на каждый запрос — это ещё 1 SQL на 5 секунд. 
        # Кеш в памяти — 1 SQL при старте.
        self.atc_level4_counts = self._load_atc_level4_counts()

        # --- Справочники ---
        self.producer_canon = self._load_producer_canon()  # origin_name → etalon_name (нормализация производителей: OOO ОЗОН → ООО ОЗОН)
        self.country_groups = self._load_country_groups()  # country_name → country_group (РОССИЯ → Russia)
        self.unit_canon = self._load_unit_canon()          # okei_code → (unit_type, etalon_name) (796 → ('count', 'шт.'))

        # --- Медианы динамики ---
        # медианы динамики. Зачем: если у нового СМНН нет данных в drug_sales / drug_stock — подставляем медианы, чтобы модель не упала.
        self.dynamic_medians = self._load_dynamic_medians()

    # ========================================================
    # ПУБЛИЧНЫЙ МЕТОД
    # ========================================================

    def build_features(self, smnn_code: str) -> Optional[pd.DataFrame]:
        df_static = self._load_static(smnn_code)
        if df_static.empty:
            return None

        static_features = self._compute_static_features(df_static)

        df_sales, df_stock = self._load_dynamic(smnn_code)
        dynamic_available = (not df_sales.empty) and (not df_stock.empty)

        if dynamic_available:
            dynamic_features = self._compute_dynamic_features(df_sales, df_stock)
        else:
            dynamic_features = self.dynamic_medians.copy()

        all_features = {**static_features, **dynamic_features}
        result = self._apply_onehot(all_features)
        result.attrs['dynamic_available'] = dynamic_available
        return result

    # ========================================================
    # ЗАГРУЗКА СТАТИКИ
    # ========================================================

    def _load_static(self, smnn_code: str) -> pd.DataFrame:
        """Загружает все актуальные drug_products для одного СМНН."""
        query = text("""
            SELECT
                dp.id_original,
                dp.name_trade,
                dp.standard_inn,
                dp.standard_form,
                dp.standard_doze,
                dp.name_producer,
                dp.country,
                dp.number_registration,
                dp.date_registration,
                dp.okei_code,
                dp.code_atc,
                dp.essential_medicines,
                dp.narcotic_psychotropic
            FROM raw_data.drug_products dp
            WHERE dp.smnn_code = :smnn_code
              AND dp.actual = TRUE
        """)
        with self.engine.connect() as conn:
            df = pd.read_sql(query, conn, params={'smnn_code': smnn_code})
        return df

    # ========================================================
    # РАСЧЁТ СТАТИЧЕСКИХ ПРИЗНАКОВ
    # ========================================================

    def _compute_static_features(self, df: pd.DataFrame) -> dict:
        """ Расчёт 26 статических признаков"""
        n = len(df)

        # --- НОРМАЛИЗАЦИЯ ПРОИЗВОДИТЕЛЯ (как в ноутбуке!) ---
        producer_norm = df['name_producer'].map(self.producer_canon)
        # Если что-то не нашлось — оставляем оригинал (fallback)
        producer_norm = producer_norm.fillna(df['name_producer'])

        # --- Счётчики ---
        products_count = n
        producers_count = producer_norm.nunique()      # ← по НОРМАЛИЗОВАННОМУ!
        trade_count = df['name_trade'].nunique()
        form_count = df['standard_form'].nunique()
        doze_count = df['standard_doze'].nunique()
        reg_num_count = df['number_registration'].nunique()
        # countries_count = df['country'].nunique()
        countries_count = df['country'].map(self.country_groups).nunique()   # ← СТАЛО (группы стран)

        # --- ATC ---
        atc_raw = df['code_atc']
        atc_first = atc_raw.apply(extract_first_valid_atc)
        atc_level4_series = atc_first.apply(normalize_atc_level4)
        atc_clean = atc_level4_series.fillna('Unknown')       # → 5 символов или 'Unknown'

        # ВАЖНО: 'Unknown' (7 символов) → str[:5] → 'Unkno'
        atc_level4_clean = atc_clean.str[:5]                  # → 5 символов или 'Unkno'

        # --- Категориальные (моды) ---
        atc_group_mode = safe_mode(atc_level4_clean.str[:1]) or 'U'  # первая буква (U для Unknown)
        atc_level4_mode = safe_mode(atc_level4_clean) or 'Unkno'     # 5 символов

        # country_group_mode — по country_group (как в ноутбуке!)
        country_group_series = df['country'].map(self.country_groups)
        country_group_mode = safe_mode(country_group_series) or 'Other'

        # has_russian_producer — по is_russian (как в ноутбуке!)
        is_russian_series = df['country'].map(
            lambda c: self.country_groups.get(c) == 'Russia'
        )
        has_russian_producer = int(is_russian_series.any())

        # primary_unit_type / name — из unit_canon
        okei_mode = safe_mode(df['okei_code'])
        primary_unit_type, primary_unit_name = self.unit_canon.get(
            int(okei_mode) if okei_mode is not None else -1,
            ('count', 'шт.')
        )

        # --- Флаги ---
        has_combined_atc = int(atc_raw.fillna('').str.contains(';').any())
        is_essential = int(df['essential_medicines'].astype(bool).any())
        is_narcotic = int(df['narcotic_psychotropic'].astype(bool).any())

        # --- Даты ---
        dates = pd.to_datetime(df['date_registration'], errors='coerce')
        if dates.notna().any():
            today = pd.Timestamp.today()
            ages = (today - dates).dt.days / 365.25
            ages = ages.dropna()
            avg_age = float(ages.mean()) if len(ages) else 0.0
            min_age = float(ages.min()) if len(ages) else 0.0
            max_age = float(ages.max()) if len(ages) else 0.0
        else:
            avg_age = min_age = max_age = 0.0

        # --- Производные ---
        log_products_count = float(np.log1p(products_count))
        log_reg_num_count = float(np.log1p(reg_num_count))
        log_trade_count = float(np.log1p(trade_count))
        log_producers_count = float(np.log1p(producers_count))

        products_per_producer = products_count / max(producers_count, 1)
        log_products_per_producer = float(np.log1p(products_per_producer))
        reg_num_per_producer = reg_num_count / max(producers_count, 1)
        trade_per_producer = trade_count / max(producers_count, 1)

        # --- ATC-признаки (замена interchange_members) ---
        atc_is_unknown = int(atc_level4_mode == 'Unkno')
        total_in_group = self.atc_level4_counts.get(atc_level4_mode, 0)
        atc_substitutes_count = max(total_in_group - 1, 0)
        # Для Unknown — 0 (как в ноутбуке)
        if atc_is_unknown:
            atc_substitutes_count = 0
        has_atc_substitutes = int(atc_substitutes_count > 0)
        log_atc_substitutes_count = float(np.log1p(atc_substitutes_count))

        return {
            'products_count': products_count,
            'producers_count': producers_count,
            'trade_count': trade_count,
            'form_count': form_count,
            'doze_count': doze_count,
            'reg_num_count': reg_num_count,
            'countries_count': countries_count,
            'has_combined_atc': has_combined_atc,
            'is_essential': is_essential,
            'is_narcotic': is_narcotic,
            'has_russian_producer': has_russian_producer,
            'atc_group_mode': atc_group_mode,
            'country_group_mode': country_group_mode,
            'primary_unit_type': primary_unit_type,
            'primary_unit_name': primary_unit_name,
            'avg_age': avg_age,
            'min_age': min_age,
            'max_age': max_age,
            'log_products_count': log_products_count,
            'log_reg_num_count': log_reg_num_count,
            'log_trade_count': log_trade_count,
            'log_producers_count': log_producers_count,
            'products_per_producer': products_per_producer,
            'log_products_per_producer': log_products_per_producer,
            'reg_num_per_producer': reg_num_per_producer,
            'trade_per_producer': trade_per_producer,
            'atc_is_unknown': atc_is_unknown,
            'atc_substitutes_count': atc_substitutes_count,
            'has_atc_substitutes': has_atc_substitutes,
            'log_atc_substitutes_count': log_atc_substitutes_count,
        }

    # ========================================================
    # ДИНАМИКА
    # ========================================================

    def _load_dynamic(self, smnn_code: str):
        """2 SQL-запроса — последние 3 месяца продаж и остатков."""
        q_sales = text("""
            SELECT 
                month, 
                sales_quantity 
            FROM synthetic_data.drug_sales
            WHERE smnn_code = :smnn_code ORDER BY month DESC LIMIT 3
        """)
        q_stock = text("""
            SELECT 
                month, 
                stock_quantity 
            FROM synthetic_data.drug_stock
            WHERE smnn_code = :smnn_code ORDER BY month DESC LIMIT 3
        """)
        with self.engine.connect() as conn:
            df_sales = pd.read_sql(q_sales, conn, params={'smnn_code': smnn_code})
            df_stock = pd.read_sql(q_stock, conn, params={'smnn_code': smnn_code})
        df_sales = df_sales.sort_values('month').reset_index(drop=True)
        df_stock = df_stock.sort_values('month').reset_index(drop=True)
        return df_sales, df_stock

    def _compute_dynamic_features(self, df_sales, df_stock) -> dict:
        sales = df_sales['sales_quantity'].values.astype(float)
        stock = df_stock['stock_quantity'].values.astype(float)

    
    
        sales_last_month = int(sales[-1])
        avg_sales_3m = float(sales.mean())
        sales_trend = float(sales[-1] / sales[-2]) if len(sales) >= 2 and sales[-2] > 0 else 1.0

        current_stock = int(stock[-1])
        avg_stock_3m = float(stock.mean())
        stock_trend = float(stock[-1] / stock[-2]) if len(stock) >= 2 and stock[-2] > 0 else 1.0

        return {
            'sales_last_month': sales_last_month,
            'current_stock': current_stock,
            'sales_trend': sales_trend,
            'stock_trend': stock_trend,
            'avg_sales_3m': avg_sales_3m,
            'avg_stock_3m': avg_stock_3m,
        }

    # ========================================================
    # ONEHOT + ПОРЯДОК
    # ========================================================

    def _apply_onehot(self, features: dict) -> pd.DataFrame:
        numeric = {k: v for k, v in features.items() if k not in OHE_COLS}
        categ = {k: v for k, v in features.items() if k in OHE_COLS}

        df_cat = pd.DataFrame([categ], columns=OHE_COLS)
        ohe_arr = self.ohe.transform(df_cat)
        ohe_names = self.ohe.get_feature_names_out(OHE_COLS)
        ohe_dict = dict(zip(ohe_names, ohe_arr[0].astype(int)))

        all_features = {**numeric, **ohe_dict}
        df_result = pd.DataFrame([all_features])

        for col in self.honest_features:
            if col not in df_result.columns:
                df_result[col] = 0

        return df_result[self.honest_features]

    # ========================================================
    # КЕШИ И СПРАВОЧНИКИ
    # ========================================================

    def _load_atc_level4_counts(self) -> dict:
        """
        Кеш: сколько УНИКАЛЬНЫХ СМНН имеют каждый ATC-level4 (5 символов).
        
        ВАЖНО: использует ту же функцию extract_first_valid_atc, что и при 
        инференсе, чтобы гарантировать согласованность.
        
        Пример: '~;R02AA20' → 'R02AA20' → 'R02AA' (а не ';R02A').
        """
        query = text("""
            SELECT DISTINCT 
                smnn_code, 
                code_atc
            FROM raw_data.drug_products
            WHERE actual = TRUE
            AND code_atc IS NOT NULL
            AND code_atc != ''
            AND code_atc != '~'
        """)
        with self.engine.connect() as conn:
            df = pd.read_sql(query, conn)
        
        df['atc_first'] = df['code_atc'].apply(extract_first_valid_atc)
        df['atc_level4'] = df['atc_first'].apply(normalize_atc_level4)
        df['atc_level4'] = df['atc_level4'].fillna('Unkno')
        
        return df['atc_level4'].value_counts().to_dict()

    def _load_producer_canon(self) -> dict:
        query = text("""
            SELECT origin_name, etalon_name FROM ref_data.producer_canon
        """)
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(query, conn)
            return dict(zip(df['origin_name'], df['etalon_name']))
        except Exception as e:
            logger.warning(f"producer_canon не загружен: {e}")
            return {}

    def _load_country_groups(self) -> dict:
        query = text("""
            SELECT 
                country_name, 
                country_group 
            FROM ref_data.country_groups
        """)
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(query, conn)
            return dict(zip(df['country_name'], df['country_group']))
        except Exception as e:
            logger.warning(f"country_groups не загружен: {e}")
            return {}

    def _load_unit_canon(self) -> dict:
        query = text("""
            SELECT
                okei_code, 
                unit_type, 
                etalon_name
            FROM ref_data.unit_canon
        """)
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(query, conn)
            return {
                int(row['okei_code']): (row['unit_type'], row['etalon_name'])
                for _, row in df.iterrows()
            }
        except Exception as e:
            logger.warning(f"unit_canon не загружен: {e}")
            return {}

    def _load_dynamic_medians(self) -> dict:
        """ 
        Считает медианы (50-й перцентиль) по последним 3 месяцам всех 7,672 СМНН.
        Зачем: если у нового СМНН нет динамики — подставляем медианы. Это «типичные» значения. Модель не падает, 
        но флаг dynamic_available = False сигнализирует API, что прогноз менее надёжен.
        """
        query = text("""
            WITH last_month AS (
                SELECT MAX(month) AS max_month FROM synthetic_data.drug_sales
            ),
            last_3m AS (
                SELECT 
                    month, 
                    sales_quantity, 
                    stock_quantity
                FROM synthetic_data.drug_sales
                JOIN synthetic_data.drug_stock USING (smnn_code, month)
                WHERE month >= (SELECT max_month - INTERVAL '2 months' FROM last_month)
            )
            SELECT
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY sales_quantity)
                    FILTER (WHERE month = (SELECT MAX(month) FROM last_3m)) AS m1,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY sales_quantity) AS m2,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY stock_quantity)
                    FILTER (WHERE month = (SELECT MAX(month) FROM last_3m)) AS m3,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY stock_quantity) AS m4
            FROM last_3m
        """)
        with self.engine.connect() as conn:
            row = conn.execute(query).fetchone()
        return {
            'sales_last_month': int(row[0] or 0),
            'avg_sales_3m': float(row[1] or 0),
            'current_stock': int(row[2] or 0),
            'avg_stock_3m': float(row[3] or 0),
            'sales_trend': 1.0,
            'stock_trend': 1.0,
        }


# ============================================================
# СИНГЛТОН
# ============================================================

_builder_instance: Optional[FeatureBuilder] = None


def init_feature_builder(db_engine, ohe_path='models/onehot_encoder_v1.pkl', metadata_path='models/metadata_v1.json'):
    """
    Что это: синглтон — глобальная переменная, которая создаётся один раз при старте FastAPI.
    Зачем: FeatureBuilder.__init__ делает 5+ SQL-запросов (кеши, справочники). 
    Если создавать на каждый запрос — это 5 сек × N запросов. Синглтон — 5 сек при старте, потом 0.
    """
    
    global _builder_instance
    _builder_instance = FeatureBuilder(db_engine, ohe_path, metadata_path)


def build_features(smnn_code: str) -> Optional[pd.DataFrame]:
    if _builder_instance is None:
        raise RuntimeError("FeatureBuilder не инициализирован")
    return _builder_instance.build_features(smnn_code)

def get_builder() -> FeatureBuilder:
    """Доступ к синглтону (для /api/health и отладки)."""
    if _builder_instance is None:
        raise RuntimeError("FeatureBuilder не инициализирован")
    return _builder_instance