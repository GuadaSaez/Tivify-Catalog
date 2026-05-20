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
OMDB_API_KEY = st.secrets.get("OMDB_API_KEY", "")

st.title("📺 Buscador de catálogo")

# -------------------------
# ESTADO INICIAL
# -------------------------
if "search" not in st.session_state:
    st.session_state.search = ""
if "selected_type" not in st.session_state:
    st.session_state.selected_type = "Todos"
if "selected_show_class" not in st.session_state:
    st.session_state.selected_show_class = "Todos"
if "selected_country" not in st.session_state:
    st.session_state.selected_country = "Todos"
if "unique_titles" not in st.session_state:
    st.session_state.unique_titles = False
if "only_tmdb" not in st.session_state:
    st.session_state.only_tmdb = False
if "only_movies" not in st.session_state:
    st.session_state.only_movies = False
if "only_cannes" not in st.session_state:
    st.session_state.only_cannes = False
if "df_catalogo" not in st.session_state:
    st.session_state.df_catalogo = None


def limpiar_filtros():
    st.session_state.search = ""
    st.session_state.selected_type = "Todos"
    st.session_state.selected_show_class = "Todos"
    st.session_state.selected_country = "Todos"
    st.session_state.unique_titles = False
    st.session_state.only_tmdb = False
    st.session_state.only_movies = False
    st.session_state.only_cannes = False


# -------------------------
# UTILIDADES
# -------------------------
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

        vote_count = r.get("vote_count") or 0
        popularity = r.get("popularity") or 0
        score_popularidad = min(float(vote_count) / 100000, 0.05) + min(float(popularity) / 10000, 0.03)

        score_total = score_titulo + score_year + score_popularidad

        if score_total > mejor_score:
            mejor_score = score_total
            mejor = r

    if mejor_score < 0.45:
        return None

    return mejor


def deduplicar_para_ranking(df):
    df_rank = df.copy()

    if "title_display" in df_rank.columns:
        campo_unico = "title_display"
    elif "tmdb_title_es" in df_rank.columns:
        campo_unico = "tmdb_title_es"
    else:
        campo_unico = "original_title"

    df_rank = df_rank.sort_values(
        by=["tmdb_vote_average", "tmdb_vote_count", "tmdb_popularity"],
        ascending=[False, False, False],
        na_position="last"
    )

    df_rank = df_rank.drop_duplicates(subset=[campo_unico])
    return df_rank


# -------------------------
# CLASIFICACIÓN SHOWS
# -------------------------
GENRES_FICTION = {
    "Drama", "Comedia", "Comedy", "Crimen", "Crime", "Misterio", "Mystery",
    "Ciencia ficción", "Sci-Fi & Fantasy", "Fantasía", "Fantasy",
    "Acción y aventura", "Action & Adventure", "Animation", "Animación",
    "Family", "Kids", "Suspense", "Thriller"
}

GENRES_PROGRAM = {
    "Documental", "Documentary", "News", "Reality", "Talk", "Soap"
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

    if genres.intersection(GENRES_PROGRAM):
        return "programa"

    if genres.intersection(GENRES_FICTION) and cast:
        return "ficcion"

    return "dudoso"


# -------------------------
# OMDB AWARDS RAW
# -------------------------
@st.cache_data(show_spinner=False)
def buscar_omdb_awards_raw(title, year, object_type, api_key):
    if not api_key or not title:
        return {"awards_raw": None, "omdb_match": False}

    omdb_type = "movie" if object_type == "movie" else "series"

    params = {
        "apikey": api_key,
        "t": title,
        "type": omdb_type,
        "r": "json",
    }

    if pd.notna(year):
        try:
            params["y"] = int(year)
        except Exception:
            pass

    try:
        response = requests.get(
            "https://www.omdbapi.com/",
            params=params,
            timeout=30
        )
        response.raise_for_status()
        data = response.json()

        if data.get("Response") != "True":
            return {"awards_raw": None, "omdb_match": False}

        return {
            "awards_raw": data.get("Awards"),
            "omdb_match": True,
        }

    except Exception:
        return {"awards_raw": None, "omdb_match": False}


# -------------------------
# WIKIDATA CANNES
# -------------------------
@st.cache_data(show_spinner=False)
def buscar_cannes_wikidata(title, year):
    if not title:
        return {
            "cannes_match": False,
            "cannes_awards": None,
            "cannes_events": None,
            "cannes_years": None,
        }

    title_escaped = str(title).replace('"', '\\"')

    year_filter = ""
    if pd.notna(year):
        try:
            year_int = int(year)
            year_filter = f"""
            OPTIONAL {{ ?film wdt:P577 ?releaseDate. }}
            FILTER(!BOUND(?releaseDate) || YEAR(?releaseDate) = {year_int} || YEAR(?releaseDate) = {year_int - 1} || YEAR(?releaseDate) = {year_int + 1})
            """
        except Exception:
            year_filter = ""

    query = f"""
    SELECT ?film ?filmLabel ?awardLabel ?eventLabel ?pointInTime WHERE {{
      ?film rdfs:label "{title_escaped}"@en.
      ?film wdt:P31/wdt:P279* wd:Q11424.

      {year_filter}

      OPTIONAL {{ ?film wdt:P166 ?award. }}
      OPTIONAL {{ ?award rdfs:label ?awardLabel FILTER(LANG(?awardLabel)="en") }}

      OPTIONAL {{ ?film wdt:P1344 ?event. }}
      OPTIONAL {{ ?event rdfs:label ?eventLabel FILTER(LANG(?eventLabel)="en") }}

      OPTIONAL {{ ?film wdt:P585 ?pointInTime. }}

      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,es". }}

      FILTER(
        CONTAINS(LCASE(STR(?awardLabel)), "cannes") ||
        CONTAINS(LCASE(STR(?eventLabel)), "cannes")
      )
    }}
    """

    params = {
        "query": query,
        "format": "json"
    }

    try:
        response = requests.get(
            "https://query.wikidata.org/sparql",
            params=params,
            timeout=30,
            headers={"User-Agent": "TivifyCatalogApp/1.0"}
        )
        response.raise_for_status()
        data = response.json()
        rows = data.get("results", {}).get("bindings", [])

        if not rows:
            return {
                "cannes_match": False,
                "cannes_awards": None,
                "cannes_events": None,
                "cannes_years": None,
            }

        awards = []
        events = []
        years = []

        for row in rows:
            if "awardLabel" in row:
                awards.append(row["awardLabel"]["value"])
            if "eventLabel" in row:
                events.append(row["eventLabel"]["value"])
            if "pointInTime" in row:
                years.append(row["pointInTime"]["value"][:4])

        return {
            "cannes_match": True,
            "cannes_awards": ", ".join(sorted(set(awards))) if awards else None,
            "cannes_events": ", ".join(sorted(set(events))) if events else None,
            "cannes_years": ", ".join(sorted(set(years))) if years else None,
        }

    except Exception:
        return {
            "cannes_match": False,
            "cannes_awards": None,
            "cannes_events": None,
            "cannes_years": None,
        }


# -------------------------
# CARGA JSON
# -------------------------
@st.cache_data
def cargar_json(url):
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


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
        "tmdb_countries": None,
        "tmdb_country_codes": None,
        "tmdb_overview_es": None,
        "tmdb_vote_average": None,
        "tmdb_vote_count": None,
        "tmdb_popularity": None,
        "show_classification": None,
    }

    for col, default_value in columnas_tmdb.items():
        if col not in df.columns:
            df[col] = default_value

    columnas_omdb = {
        "awards_raw": None,
        "omdb_match": False,
    }

    for col, default_value in columnas_omdb.items():
        if col not in df.columns:
            df[col] = default_value

    columnas_cannes = {
        "cannes_match": False,
        "cannes_awards": None,
        "cannes_events": None,
        "cannes_years": None,
    }

    for col, default_value in columnas_cannes.items():
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
            "tmdb_countries": None,
            "tmdb_country_codes": None,
            "tmdb_overview_es": None,
            "tmdb_vote_average": None,
            "tmdb_vote_count": None,
            "tmdb_popularity": None,
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

    production_countries = details_data.get("production_countries", [])
    country_names = []
    country_codes = []

    for c in production_countries:
        name = c.get("name")
        code = c.get("iso_3166_1")
        if name:
            country_names.append(name)
        if code:
            country_codes.append(code)

    if not country_codes and endpoint == "tv":
        origin_country = details_data.get("origin_country", [])
        if isinstance(origin_country, list):
            country_codes.extend(origin_country)

    tmdb_countries = ", ".join(sorted(set(country_names))) if country_names else None
    tmdb_country_codes = ", ".join(sorted(set(country_codes))) if country_codes else None

    tmdb_overview_es = details_data.get("overview")
    tmdb_vote_average = details_data.get("vote_average")
    tmdb_vote_count = details_data.get("vote_count")
    tmdb_popularity = details_data.get("popularity")

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
        "tmdb_countries": tmdb_countries,
        "tmdb_country_codes": tmdb_country_codes,
        "tmdb_overview_es": tmdb_overview_es,
        "tmdb_vote_average": tmdb_vote_average,
        "tmdb_vote_count": tmdb_vote_count,
        "tmdb_popularity": tmdb_popularity,
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
        "tmdb_countries": detalles["tmdb_countries"],
        "tmdb_country_codes": detalles["tmdb_country_codes"],
        "tmdb_overview_es": detalles["tmdb_overview_es"],
        "tmdb_vote_average": detalles["tmdb_vote_average"],
        "tmdb_vote_count": detalles["tmdb_vote_count"],
        "tmdb_popularity": detalles["tmdb_popularity"],
        "tmdb_match": True,
    }


# -------------------------
# TMDB SERIES
# -------------------------
@st.cache_data(show_spinner=False)
def buscar_tmdb_tv(title, year, api_key):
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
        "tmdb_countries": detalles["tmdb_countries"],
        "tmdb_country_codes": detalles["tmdb_country_codes"],
        "tmdb_overview_es": detalles["tmdb_overview_es"],
        "tmdb_vote_average": detalles["tmdb_vote_average"],
        "tmdb_vote_count": detalles["tmdb_vote_count"],
        "tmdb_popularity": detalles["tmdb_popularity"],
        "tmdb_match": True,
    }


# -------------------------
# BÚSQUEDA MULTI TMDB
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
        "tmdb_countries": None,
        "tmdb_country_codes": None,
        "tmdb_overview_es": None,
        "tmdb_vote_average": None,
        "tmdb_vote_count": None,
        "tmdb_popularity": None,
        "tmdb_match": False,
    }


# -------------------------
# FILTROS
# -------------------------
def aplicar_filtros(df, search, selected_type, unique_titles, only_tmdb, selected_show_class, selected_country, only_movies=False, only_cannes=False):
    df_filtrado = df.copy()

    if search:
        mask = pd.Series(False, index=df_filtrado.index)

        for campo in ["original_title", "tmdb_title_es", "title_display"]:
            if campo in df_filtrado.columns:
                mask = mask | df_filtrado[campo].astype(str).str.contains(search, case=False, na=False)

        df_filtrado = df_filtrado[mask]

    if selected_type != "Todos" and "object_type" in df_filtrado.columns:
        df_filtrado = df_filtrado[df_filtrado["object_type"] == selected_type]

    if only_movies and "object_type" in df_filtrado.columns:
        df_filtrado = df_filtrado[df_filtrado["object_type"] == "movie"]

    if selected_show_class != "Todos" and "show_classification" in df_filtrado.columns:
        df_filtrado = df_filtrado[df_filtrado["show_classification"] == selected_show_class]

    if selected_country != "Todos" and "tmdb_countries" in df_filtrado.columns:
        mask_country = (
            df_filtrado["tmdb_countries"].astype(str).str.contains(selected_country, case=False, na=False)
            | df_filtrado["tmdb_country_codes"].astype(str).str.contains(selected_country, case=False, na=False)
        )
        df_filtrado = df_filtrado[mask_country]

    if only_cannes and "cannes_match" in df_filtrado.columns:
        df_filtrado = df_filtrado[df_filtrado["cannes_match"] == True]

    if unique_titles:
        campo_unico = "title_display" if "title_display" in df_filtrado.columns else "original_title"
        df_filtrado = df_filtrado.drop_duplicates(subset=[campo_unico])

    if only_tmdb and "tmdb_match" in df_filtrado.columns:
        df_filtrado = df_filtrado[df_filtrado["tmdb_match"] == True]

    return df_filtrado


# -------------------------
# ENRIQUECER
# -------------------------
def enriquecer_filtro_actual(df, api_key, search, selected_type, unique_titles, selected_show_class, selected_country, only_movies=False, only_cannes=False, max_items=None):
    df = df.copy()

subset = aplicar_filtros(
    df,
    search=search,
    selected_type=selected_type,
    unique_titles=unique_titles,
    only_tmdb=False,
    selected_show_class=selected_show_class,
    selected_country=selected_country,
    only_movies=only_movies,
    only_cannes=False
)

    subset = subset[subset["tmdb_match"] != True]

    if max_items is not None:
        subset = subset.head(max_items)

    total = len(subset)

    if total == 0:
        return df, 0

    progress = st.progress(0, text="Enriqueciendo filtro con TMDB, OMDb y Cannes...")

    for i, (idx, row) in enumerate(subset.iterrows(), start=1):
        result = buscar_tmdb_multi(row, api_key)

        df.at[idx, "tmdb_id"] = result["tmdb_id"]
        df.at[idx, "tmdb_title_es"] = result["tmdb_title_es"]
        df.at[idx, "tmdb_cast"] = result["tmdb_cast"]
        df.at[idx, "tmdb_genres"] = result["tmdb_genres"]
        df.at[idx, "tmdb_countries"] = result["tmdb_countries"]
        df.at[idx, "tmdb_country_codes"] = result["tmdb_country_codes"]
        df.at[idx, "tmdb_overview_es"] = result["tmdb_overview_es"]
        df.at[idx, "tmdb_vote_average"] = result["tmdb_vote_average"]
        df.at[idx, "tmdb_vote_count"] = result["tmdb_vote_count"]
        df.at[idx, "tmdb_popularity"] = result["tmdb_popularity"]
        df.at[idx, "tmdb_match"] = result["tmdb_match"]

        awards_result = buscar_omdb_awards_raw(
            row.get("original_title"),
            row.get("release_year"),
            row.get("object_type"),
            OMDB_API_KEY
        )

        df.at[idx, "awards_raw"] = awards_result["awards_raw"]
        df.at[idx, "omdb_match"] = awards_result["omdb_match"]

        cannes_result = buscar_cannes_wikidata(
            row.get("original_title"),
            row.get("release_year")
        )

        df.at[idx, "cannes_match"] = cannes_result["cannes_match"]
        df.at[idx, "cannes_awards"] = cannes_result["cannes_awards"]
        df.at[idx, "cannes_events"] = cannes_result["cannes_events"]
        df.at[idx, "cannes_years"] = cannes_result["cannes_years"]

        progress.progress(i / total, text=f"Enriqueciendo filtro con TMDB, OMDb y Cannes... {i}/{total}")
        time.sleep(0.03)

    df["title_display"] = df["tmdb_title_es"].fillna(df["title_final"])

    for col in ["tmdb_vote_average", "tmdb_vote_count", "tmdb_popularity"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "object_type" in df.columns:
        mask_show = df["object_type"] == "show"
        df.loc[mask_show, "show_classification"] = df.loc[mask_show].apply(clasificar_show, axis=1)

    return df, total


def convertir_a_csv(df):
    return df.to_csv(index=False).encode("utf-8-sig")


# -------------------------
# UI
# -------------------------
json_url = st.text_input(
    "Pega la URL del JSON",
    value="https://mediasync.tvup.cloud/mexport/justwatch/Tivify%20B2C.json"
)

top1, top2, top3 = st.columns([1, 1, 1])

with top1:
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

with top3:
    if st.button("Limpiar filtros"):
        limpiar_filtros()
        st.rerun()

if st.session_state.df_catalogo is not None:
    df = st.session_state.df_catalogo.copy()

    for col in ["tmdb_vote_average", "tmdb_vote_count", "tmdb_popularity"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "object_type" in df.columns:
        mask_show = df["object_type"] == "show"
        df.loc[mask_show, "show_classification"] = df.loc[mask_show].apply(clasificar_show, axis=1)

    st.write(f"Número de contenidos tras limpieza editorial: {len(df)}")

    st.subheader("Filtros")

    search = st.text_input("🔎 Buscar por título", key="search")

    object_types = []
    if "object_type" in df.columns:
        object_types = sorted(df["object_type"].dropna().unique().tolist())

    selected_type = st.selectbox("Tipo de contenido", ["Todos"] + object_types, key="selected_type")

    show_class_options = ["Todos", "ficcion", "programa", "dudoso"]
    selected_show_class = st.selectbox("Clasificación de shows", show_class_options, key="selected_show_class")

    country_options = ["Todos"]
    if "tmdb_countries" in df.columns:
        countries = set()
        for value in df["tmdb_countries"].dropna():
            for c in str(value).split(","):
                c = c.strip()
                if c and c.lower() != "none":
                    countries.add(c)
        country_options += sorted(countries)

    selected_country = st.selectbox("País TMDB", country_options, key="selected_country")

    unique_titles = st.checkbox("Mostrar solo títulos únicos", key="unique_titles")
    only_tmdb = st.checkbox("Mostrar solo títulos enriquecidos con TMDB", key="only_tmdb")
    only_movies = st.checkbox("Mostrar solo películas", key="only_movies")
    only_cannes = st.checkbox("Mostrar solo títulos con Cannes", key="only_cannes")

    with top2:
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
                        selected_country=selected_country,
                        only_movies=only_movies,
                        only_cannes=only_cannes,
                        max_items=None
                    )
                    st.session_state.df_catalogo = df_actualizado
                    df = df_actualizado
                    st.success(f"Filtro enriquecido con TMDB, OMDb y Cannes ✅ ({n_enriquecidos} títulos procesados)")
                except Exception as e:
                    st.error(f"Error al enriquecer el filtro: {e}")

    df_filtrado = aplicar_filtros(
        df,
        search=search,
        selected_type=selected_type,
        unique_titles=unique_titles,
        only_tmdb=only_tmdb,
        selected_show_class=selected_show_class,
        selected_country=selected_country,
        only_movies=only_movies,
        only_cannes=only_cannes
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
            "tmdb_vote_average",
            "tmdb_vote_count",
            "tmdb_popularity",
            "tmdb_cast",
            "tmdb_genres",
            "tmdb_countries",
            "tmdb_country_codes",
            "awards_raw",
            "cannes_match",
            "cannes_awards",
            "cannes_events",
            "cannes_years",
            "tmdb_overview_es",
            "tmdb_match"
        ] if col in df_filtrado.columns
    ]

    st.subheader("Resultados")
    st.write(f"Resultados encontrados: {len(df_filtrado)}")

    num_rows = len(df_filtrado)
    altura_tabla = min(80 + num_rows * 35, 600)

    st.dataframe(
        df_filtrado[columnas_mostrar],
        width=2800,
        height=altura_tabla
    )

    csv_data = convertir_a_csv(df_filtrado[columnas_mostrar])

    st.download_button(
        label="⬇️ Descargar resultados filtrados en CSV",
        data=csv_data,
        file_name="catalogo_filtrado_enriquecido.csv",
        mime="text/csv"
    )

    # -------------------------
    # TOP 50 TMDB
    # -------------------------
    st.subheader("🏆 Top 50 por nota TMDB")

    df_top_base = df_filtrado.copy()

    if "object_type" in df_top_base.columns:
        df_top_base = df_top_base[df_top_base["object_type"] == "movie"]

    if "tmdb_match" in df_top_base.columns:
        df_top_base = df_top_base[df_top_base["tmdb_match"] == True]

    for col in ["tmdb_vote_average", "tmdb_vote_count", "tmdb_popularity"]:
        if col in df_top_base.columns:
            df_top_base[col] = pd.to_numeric(df_top_base[col], errors="coerce")

    df_top_base = df_top_base.dropna(subset=["tmdb_vote_average"])
    df_top_base = df_top_base[df_top_base["tmdb_vote_count"].fillna(0) > 0]

    df_top_base = deduplicar_para_ranking(df_top_base)

    df_top50 = df_top_base.sort_values(
        by=["tmdb_vote_average", "tmdb_vote_count", "tmdb_popularity"],
        ascending=[False, False, False],
        na_position="last"
    ).head(50)

    columnas_top50 = [
        col for col in [
            "title_display",
            "original_title",
            "release_year",
            "tmdb_vote_average",
            "tmdb_vote_count",
            "tmdb_popularity",
            "tmdb_genres",
            "tmdb_countries",
            "director",
            "awards_raw",
            "cannes_match",
            "cannes_awards",
            "cannes_events",
            "cannes_years",
            "tmdb_overview_es"
        ] if col in df_top50.columns
    ]

    st.write(f"Películas disponibles para ranking TMDB: {len(df_top_base)}")

    st.dataframe(
        df_top50[columnas_top50],
        width=2400,
        height=min(80 + len(df_top50) * 35, 600)
    )

    st.download_button(
        label="⬇️ Descargar Top 50 TMDB en CSV",
        data=convertir_a_csv(df_top50[columnas_top50]),
        file_name="top_50_tmdb.csv",
        mime="text/csv"
    )
