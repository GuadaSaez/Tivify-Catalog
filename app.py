import streamlit as st
import requests
import pandas as pd
import time
import json
import re
import unicodedata
from difflib import SequenceMatcher

st.set_page_config(page_title="Buscador de catálogo", layout="wide")

TMDB_API_KEY = st.secrets.get("TMDB_API_KEY", "")

st.title("📺 Buscador de catálogo")

# -------------------------
# CARGA JSON
# -------------------------
@st.cache_data
def cargar_json(url):
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.json()

# -------------------------
# UTILIDADES
# -------------------------
def get_spanish_title(x):
    if isinstance(x, dict):
        for key in ["es", "es-ES", "ES", "spa", "spanish"]:
            if key in x and x[key]:
                return x[key]
    return None

def crew_to_json(x):
    try:
        if isinstance(x, (list, dict)):
            return json.dumps(x, ensure_ascii=False)
        return str(x) if x is not None else None
    except Exception:
        return None

def extraer_director(x):
    if not isinstance(x, list):
        return None

    directores = []

    for item in x:
        if not isinstance(item, dict):
            continue

        nombre = (
            item.get("name")
            or item.get("full_name")
            or item.get("person_name")
            or item.get("title")
        )

        rol = str(
            item.get("role")
            or item.get("job")
            or item.get("profession")
            or item.get("type")
            or ""
        ).lower()

        if nombre and any(p in rol for p in ["director", "directed", "direction", "réalisation"]):
            directores.append(nombre)

    if directores:
        return ", ".join(sorted(set(directores)))

    return None

def normalizar_titulo(texto):
    if not texto:
        return ""
    texto = str(texto).lower().strip()
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("utf-8")
    texto = re.sub(r"[^\w\s]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto

def similitud_titulo(a, b):
    a_norm = normalizar_titulo(a)
    b_norm = normalizar_titulo(b)
    if not a_norm or not b_norm:
        return 0
    return SequenceMatcher(None, a_norm, b_norm).ratio()

def elegir_mejor_resultado_tmdb(results, query_title, expected_year=None):
    if not results:
        return None

    mejor = None
    mejor_score = -1

    for r in results:
        posibles_titulos_resultado = [
            r.get("title"),
            r.get("name"),
            r.get("original_title"),
            r.get("original_name"),
        ]

        similitudes = [
            similitud_titulo(query_title, t)
            for t in posibles_titulos_resultado
            if t
        ]
        score_titulo = max(similitudes) if similitudes else 0

        score_year = 0
        if expected_year:
            try:
                expected_year_int = int(expected_year)
                fecha = r.get("release_date") or r.get("first_air_date")
                if fecha and len(str(fecha)) >= 4:
                    result_year = int(str(fecha)[:4])
                    diff = abs(expected_year_int - result_year)
                    if diff == 0:
                        score_year = 0.2
                    elif diff == 1:
                        score_year = 0.1
                    elif diff <= 3:
                        score_year = 0.05
            except Exception:
                pass

        score_total = score_titulo + score_year

        if score_total > mejor_score:
            mejor_score = score_total
            mejor = r

    if mejor_score < 0.45:
        return None

    return mejor

# -------------------------
# CLASIFICACIÓN SHOWS
# -------------------------
GENRES_FICTION = {
    "Drama", "Comedia", "Crimen", "Misterio", "Ciencia ficción", "Fantasía",
    "Acción y aventura", "Animación", "Suspense", "War & Politics",
    "Sci-Fi & Fantasy", "Action & Adventure", "Mystery", "Crime", "Drama",
    "Comedy", "Animation", "Family", "Kids"
}

GENRES_PROGRAM = {
    "Documental", "News", "Reality", "Talk", "Documentary", "Soap",
    "War & Politics"  # aquí puede haber ambigüedad, lo dejamos como no ficción salvo que haya cast fuerte
}

def clasificar_show(row):
    if row.get("object_type") != "show":
        return None

    tmdb_match = bool(row.get("tmdb_match"))
    cast = str(row.get("tmdb_cast") or "").strip()
    genres_str = str(row.get("tmdb_genres") or "").strip()

    if not tmdb_match:
        return "dudoso"

    genres = {g.strip() for g in genres_str.split(",") if g.strip()}

    # si tiene géneros claramente de ficción y además cast, muy probable que sea serie de ficción
    if genres.intersection(GENRES_FICTION) and cast:
        return "ficcion"

    # si tiene géneros claramente de programa/no ficción
    if genres.intersection(GENRES_PROGRAM):
        return "programa"

    # si tiene cast y match, aunque no tengamos género claro, suele inclinar a ficción
    if cast:
        return "ficcion"

    return "dudoso"

# -------------------------
# PREPARAR DATAFRAME
# -------------------------
def preparar_dataframe(data):
    contents = data.get("contents", [])
    df = pd.DataFrame(contents)

    if df.empty:
        return df

    if "localized_titles" in df.columns:
        df["title_es"] = df["localized_titles"].apply(get_spanish_title)
    else:
        df["title_es"] = None

    if "original_title" in df.columns:
        df["title_final"] = df["title_es"].fillna(df["original_title"])
    else:
        df["title_final"] = df["title_es"]

    if "object_type" in df.columns:
        df = df[df["object_type"].isin(["movie", "show"])]

    if "crew_members" in df.columns:
        df["crew_members_json"] = df["crew_members"].apply(crew_to_json)
        df["director"] = df["crew_members"].apply(extraer_director)
    else:
        df["crew_members_json"] = None
        df["director"] = None

    columnas_tmdb = {
        "tmdb_id": None,
        "tmdb_title_es": None,
        "tmdb_match": False,
        "tmdb_cast": None,
        "tmdb_genres": None,
        "tmdb_overview_es": None,
        "show_classification": None,
    }

    for col, default_value in columnas_tmdb.items():
        if col not in df.columns:
            df[col] = default_value

    if "title_display" not in df.columns:
        df["title_display"] = df["title_final"]

    return df

# -------------------------
# TMDB DETALLES
# -------------------------
@st.cache_data(show_spinner=False)
def obtener_detalles_tmdb(tmdb_id, endpoint, api_key):
    if not tmdb_id:
        return {
            "tmdb_cast": None,
            "tmdb_genres": None,
            "tmdb_overview_es": None,
        }

    details_url = f"https://api.themoviedb.org/3/{endpoint}/{tmdb_id}"
    details_params = {
        "api_key": api_key,
        "language": "es-ES"
    }

    details_response = requests.get(details_url, params=details_params, timeout=30)
    details_response.raise_for_status()
    details_data = details_response.json()

    genres = details_data.get("genres", [])
    tmdb_genres = ", ".join([g["name"] for g in genres if "name" in g]) if genres else None
    tmdb_overview_es = details_data.get("overview")

    credits_url = f"https://api.themoviedb.org/3/{endpoint}/{tmdb_id}/credits"
    credits_params = {
        "api_key": api_key,
        "language": "es-ES"
    }

    credits_response = requests.get(credits_url, params=credits_params, timeout=30)
    credits_response.raise_for_status()
    credits_data = credits_response.json()

    cast_list = credits_data.get("cast", [])
    top_cast = [c.get("name") for c in cast_list[:8] if c.get("name")]
    tmdb_cast = ", ".join(top_cast) if top_cast else None

    return {
        "tmdb_cast": tmdb_cast,
        "tmdb_genres": tmdb_genres,
        "tmdb_overview_es": tmdb_overview_es,
    }

# -------------------------
# TMDB MOVIES
# -------------------------
@st.cache_data(show_spinner=False)
def buscar_tmdb_movie(title, year, api_key):
    search_url = "https://api.themoviedb.org/3/search/movie"
    params = {
        "api_key": api_key,
        "query": title,
        "language": "es-ES"
    }
    if pd.notna(year):
        try:
            params["year"] = int(year)
        except Exception:
            pass

    response = requests.get(search_url, params=params, timeout=30)
    response.raise_for_status()
    results = response.json().get("results", [])

    best = elegir_mejor_resultado_tmdb(results, title, year)
    if not best:
        return None

    tmdb_id = best.get("id")
    tmdb_title_es = best.get("title") or best.get("original_title")
    detalles = obtener_detalles_tmdb(tmdb_id, "movie", api_key)

    return {
        "tmdb_id": tmdb_id,
        "tmdb_title_es": tmdb_title_es,
        "tmdb_cast": detalles["tmdb_cast"],
        "tmdb_genres": detalles["tmdb_genres"],
        "tmdb_overview_es": detalles["tmdb_overview_es"],
        "tmdb_match": True,
    }

# -------------------------
# TMDB SERIES
# -------------------------
@st.cache_data(show_spinner=False)
def buscar_tmdb_tv(title, year, api_key):
    # intento 1: search/tv sin año
    url_tv = "https://api.themoviedb.org/3/search/tv"
    params_tv = {
        "api_key": api_key,
        "query": title,
        "language": "es-ES"
    }

    response_tv = requests.get(url_tv, params=params_tv, timeout=30)
    response_tv.raise_for_status()
    results_tv = response_tv.json().get("results", [])

    best = elegir_mejor_resultado_tmdb(results_tv, title, None)

    # intento 2: search/tv con año
    if not best and pd.notna(year):
        params_tv_year = {
            "api_key": api_key,
            "query": title,
            "language": "es-ES"
        }
        try:
            params_tv_year["first_air_date_year"] = int(year)
        except Exception:
            pass

        response_tv_year = requests.get(url_tv, params=params_tv_year, timeout=30)
        response_tv_year.raise_for_status()
        results_tv_year = response_tv_year.json().get("results", [])
        best = elegir_mejor_resultado_tmdb(results_tv_year, title, year)

    # intento 3: search/multi
    if not best:
        url_multi = "https://api.themoviedb.org/3/search/multi"
        params_multi = {
            "api_key": api_key,
            "query": title,
            "language": "es-ES"
        }

        response_multi = requests.get(url_multi, params=params_multi, timeout=30)
        response_multi.raise_for_status()
        results_multi = response_multi.json().get("results", [])

        results_multi_tv = [r for r in results_multi if r.get("media_type") == "tv"]
        best = elegir_mejor_resultado_tmdb(results_multi_tv, title, year)

    if not best:
        return None

    tmdb_id = best.get("id")
    tmdb_title_es = best.get("name") or best.get("original_name")
    detalles = obtener_detalles_tmdb(tmdb_id, "tv", api_key)

    return {
        "tmdb_id": tmdb_id,
        "tmdb_title_es": tmdb_title_es,
        "tmdb_cast": detalles["tmdb_cast"],
        "tmdb_genres": detalles["tmdb_genres"],
        "tmdb_overview_es": detalles["tmdb_overview_es"],
        "tmdb_match": True,
    }

# -------------------------
# BÚSQUEDA MULTI
# -------------------------
@st.cache_data(show_spinner=False)
def buscar_tmdb_multi(row, api_key):
    posibles_titulos = []

    for campo in ["original_title", "title_display", "title_final", "title_es"]:
        valor = row.get(campo)
        if valor and str(valor).strip():
            valor = str(valor).strip()
            if valor not in posibles_titulos:
                posibles_titulos.append(valor)

            valor_lower = valor.lower()
            for prefijo in ["the ", "a ", "an ", "la ", "el ", "los ", "las "]:
                if valor_lower.startswith(prefijo):
                    sin_articulo = valor[len(prefijo):].strip()
                    if sin_articulo and sin_articulo not in posibles_titulos:
                        posibles_titulos.append(sin_articulo)

    object_type = row.get("object_type")
    release_year = row.get("release_year")

    try:
        for titulo in posibles_titulos:
            if object_type == "movie":
                result = buscar_tmdb_movie(titulo, release_year, api_key)
            else:
                result = buscar_tmdb_tv(titulo, release_year, api_key)

            if result and result.get("tmdb_match"):
                return result
    except Exception:
        pass

    return {
        "tmdb_id": None,
        "tmdb_title_es": None,
        "tmdb_cast": None,
        "tmdb_genres": None,
        "tmdb_overview_es": None,
        "tmdb_match": False,
    }

# -------------------------
# FILTROS
# -------------------------
def aplicar_filtros(df, search, selected_type, unique_titles, only_tmdb, selected_show_class):
    df_filtrado = df.copy()

    if search:
        mask = pd.Series(False, index=df_filtrado.index)

        for campo in ["original_title", "tmdb_title_es", "title_display"]:
            if campo in df_filtrado.columns:
                mask = mask | df_filtrado[campo].astype(str).str.contains(search, case=False, na=False)

        df_filtrado = df_filtrado[mask]

    if selected_type != "Todos" and "object_type" in df_filtrado.columns:
        df_filtrado = df_filtrado[df_filtrado["object_type"] == selected_type]

    if selected_show_class != "Todos" and "show_classification" in df_filtrado.columns:
        df_filtrado = df_filtrado[df_filtrado["show_classification"] == selected_show_class]

    if unique_titles:
        campo_unico = "title_display" if "title_display" in df_filtrado.columns else "original_title"
        df_filtrado = df_filtrado.drop_duplicates(subset=[campo_unico])

    if only_tmdb and "tmdb_match" in df_filtrado.columns:
        df_filtrado = df_filtrado[df_filtrado["tmdb_match"] == True]

    return df_filtrado

# -------------------------
# ENRIQUECER
# -------------------------
def enriquecer_filtro_actual(df, api_key, search, selected_type, unique_titles, selected_show_class, max_items=None):
    df = df.copy()

    subset = aplicar_filtros(
        df,
        search=search,
        selected_type=selected_type,
        unique_titles=unique_titles,
        only_tmdb=False,
        selected_show_class=selected_show_class
    )

    subset = subset[subset["tmdb_match"] != True]

    if max_items is not None:
        subset = subset.head(max_items)

    total = len(subset)

    if total == 0:
        return df, 0

    progress = st.progress(0, text="Enriqueciendo filtro con TMDB...")

    for i, (idx, row) in enumerate(subset.iterrows(), start=1):
        result = buscar_tmdb_multi(row, api_key)

        df.at[idx, "tmdb_id"] = result["tmdb_id"]
        df.at[idx, "tmdb_title_es"] = result["tmdb_title_es"]
        df.at[idx, "tmdb_cast"] = result["tmdb_cast"]
        df.at[idx, "tmdb_genres"] = result["tmdb_genres"]
        df.at[idx, "tmdb_overview_es"] = result["tmdb_overview_es"]
        df.at[idx, "tmdb_match"] = result["tmdb_match"]

        # reclasificar show si aplica
        df.at[idx, "show_classification"] = clasificar_show(df.loc[idx])

        progress.progress(i / total, text=f"Enriqueciendo filtro con TMDB... {i}/{total}")
        time.sleep(0.03)

    df["title_display"] = df["tmdb_title_es"].fillna(df["title_final"])

    # recalcular clasificación por si había títulos ya enriquecidos
    if "object_type" in df.columns:
        mask_show = df["object_type"] == "show"
        df.loc[mask_show, "show_classification"] = df.loc[mask_show].apply(clasificar_show, axis=1)

    return df, total

def convertir_a_csv(df):
    return df.to_csv(index=False).encode("utf-8-sig")

# -------------------------
# ESTADO
# -------------------------
if "df_catalogo" not in st.session_state:
    st.session_state.df_catalogo = None

# -------------------------
# UI
# -------------------------
json_url = st.text_input(
    "Pega la URL del JSON",
    value="https://mediasync.tvup.cloud/mexport/justwatch/Tivify%20B2C.json"
)

col1, col2 = st.columns(2)

with col1:
    if st.button("Cargar datos"):
        if json_url:
            try:
                with st.spinner("Descargando datos..."):
                    data = cargar_json(json_url)
                    df = preparar_dataframe(data)
                    st.session_state.df_catalogo = df
                st.success("JSON cargado correctamente ✅")
            except Exception as e:
                st.error(f"Error al cargar el JSON: {e}")
        else:
            st.error("Por favor, introduce una URL")

if st.session_state.df_catalogo is not None:
    df = st.session_state.df_catalogo.copy()

    # recalcular clasificación si ya hay datos enriquecidos
    if "object_type" in df.columns:
        mask_show = df["object_type"] == "show"
        df.loc[mask_show, "show_classification"] = df.loc[mask_show].apply(clasificar_show, axis=1)

    st.write(f"Número de contenidos tras limpieza editorial: {len(df)}")

    st.subheader("Filtros")

    search = st.text_input("🔎 Buscar por título")

    object_types = []
    if "object_type" in df.columns:
        object_types = sorted(df["object_type"].dropna().unique().tolist())

    selected_type = st.selectbox("Tipo de contenido", ["Todos"] + object_types)

    show_class_options = ["Todos", "ficcion", "programa", "dudoso"]
    selected_show_class = st.selectbox("Clasificación de shows", show_class_options)

    unique_titles = st.checkbox("Mostrar solo títulos únicos")
    only_tmdb = st.checkbox("Mostrar solo títulos enriquecidos con TMDB")

    with col2:
        if st.button("Enriquecer filtro actual con TMDB"):
            if not TMDB_API_KEY:
                st.error("No se ha encontrado la API key de TMDB en secrets.toml")
            else:
                try:
                    df_actualizado, n_enriquecidos = enriquecer_filtro_actual(
                        st.session_state.df_catalogo,
                        TMDB_API_KEY,
                        search=search,
                        selected_type=selected_type,
                        unique_titles=unique_titles,
                        selected_show_class=selected_show_class,
                        max_items=None
                    )
                    st.session_state.df_catalogo = df_actualizado
                    df = df_actualizado
                    st.success(f"Filtro enriquecido con TMDB ✅ ({n_enriquecidos} títulos procesados)")
                except Exception as e:
                    st.error(f"Error al enriquecer el filtro: {e}")

    df_filtrado = aplicar_filtros(
        df,
        search=search,
        selected_type=selected_type,
        unique_titles=unique_titles,
        only_tmdb=only_tmdb,
        selected_show_class=selected_show_class
    )

    columnas_mostrar = [
        col for col in [
            "original_title",
            "tmdb_title_es",
            "title_display",
            "object_type",
            "show_classification",
            "release_year",
            "runtime",
            "director",
            "tmdb_cast",
            "tmdb_genres",
            "tmdb_overview_es",
            "tmdb_match"
        ] if col in df_filtrado.columns
    ]

    st.subheader("Resultados")
    st.write(f"Resultados encontrados: {len(df_filtrado)}")
    st.dataframe(df_filtrado[columnas_mostrar], use_container_width=True)

    csv_data = convertir_a_csv(df_filtrado[columnas_mostrar])

    st.download_button(
        label="⬇️ Descargar resultados filtrados en CSV",
        data=csv_data,
        file_name="catalogo_filtrado_enriquecido.csv",
        mime="text/csv"
    )
