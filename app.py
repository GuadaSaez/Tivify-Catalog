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

@st.cache_data
def cargar_json(url):
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.json()

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

def elegir_mejor_resultado_tmdb(results, query_title, expected_year=None, object_type=None):
    """
    Elige el mejor resultado según:
    - similitud de título
    - cercanía de año (si existe)
    """
    if not results:
        return None

    mejor = None
    mejor_score = -1

    for r in results:
        candidate_title = r.get("title") or r.get("name") or r.get("original_title") or r.get("original_name")
        score_titulo = similitud_titulo(query_title, candidate_title)

        score_year = 0
        if expected_year:
            try:
                expected_year = int(expected_year)
                fecha = r.get("release_date") or r.get("first_air_date")
                if fecha and len(fecha) >= 4:
                    result_year = int(fecha[:4])
                    diff = abs(expected_year - result_year)
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

    # umbral mínimo para evitar matches absurdos
    if mejor_score < 0.55:
        return None

    return mejor

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
    }

    for col, default_value in columnas_tmdb.items():
        if col not in df.columns:
            df[col] = default_value

    if "title_display" not in df.columns:
        df["title_display"] = df["title_final"]

    return df

@st.cache_data(show_spinner=False)
def buscar_tmdb_y_detalles(title, year, object_type, api_key):
    if not api_key or not title or not object_type:
        return {
            "tmdb_id": None,
            "tmdb_title_es": None,
            "tmdb_cast": None,
            "tmdb_genres": None,
            "tmdb_overview_es": None,
            "tmdb_match": False,
        }

    endpoint = "movie" if object_type == "movie" else "tv"
    search_url = f"https://api.themoviedb.org/3/search/{endpoint}"

    params = {
        "api_key": api_key,
        "query": title,
        "language": "es-ES"
    }

    if pd.notna(year):
        try:
            year_int = int(year)
            if endpoint == "movie":
                params["year"] = year_int
            else:
                params["first_air_date_year"] = year_int
        except Exception:
            pass

    try:
        search_response = requests.get(search_url, params=params, timeout=30)
        search_response.raise_for_status()
        results = search_response.json().get("results", [])

        best = elegir_mejor_resultado_tmdb(
            results,
            query_title=title,
            expected_year=year,
            object_type=object_type
        )

        if not best:
            return {
                "tmdb_id": None,
                "tmdb_title_es": None,
                "tmdb_cast": None,
                "tmdb_genres": None,
                "tmdb_overview_es": None,
                "tmdb_match": False,
            }

        tmdb_id = best.get("id")
        tmdb_title_es = best.get("title") or best.get("name")
        tmdb_overview_es = best.get("overview")

        if not tmdb_id:
            return {
                "tmdb_id": None,
                "tmdb_title_es": tmdb_title_es,
                "tmdb_cast": None,
                "tmdb_genres": None,
                "tmdb_overview_es": tmdb_overview_es,
                "tmdb_match": True,
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
            "tmdb_id": tmdb_id,
            "tmdb_title_es": tmdb_title_es,
            "tmdb_cast": tmdb_cast,
            "tmdb_genres": tmdb_genres,
            "tmdb_overview_es": tmdb_overview_es,
            "tmdb_match": True,
        }

    except Exception:
        return {
            "tmdb_id": None,
            "tmdb_title_es": None,
            "tmdb_cast": None,
            "tmdb_genres": None,
            "tmdb_overview_es": None,
            "tmdb_match": False,
        }

def buscar_tmdb_multi(row, api_key):
    """
    Mejora el matching, especialmente para series:
    - prueba varios títulos posibles
    - para shows primero busca sin año
    - luego, si falla, prueba con año
    """
    posibles_titulos = []
    for campo in ["original_title", "title_display", "title_final", "title_es"]:
        valor = row.get(campo)
        if valor and str(valor).strip() and valor not in posibles_titulos:
            posibles_titulos.append(valor)

    object_type = row.get("object_type")
    release_year = row.get("release_year")

    # Para series: primero sin año
    if object_type == "show":
        for titulo in posibles_titulos:
            result = buscar_tmdb_y_detalles(
                titulo,
                None,
                object_type,
                api_key
            )
            if result.get("tmdb_match"):
                return result

        for titulo in posibles_titulos:
            result = buscar_tmdb_y_detalles(
                titulo,
                release_year,
                object_type,
                api_key
            )
            if result.get("tmdb_match"):
                return result

    # Para películas: primero con año, luego sin año
    else:
        for titulo in posibles_titulos:
            result = buscar_tmdb_y_detalles(
                titulo,
                release_year,
                object_type,
                api_key
            )
            if result.get("tmdb_match"):
                return result

        for titulo in posibles_titulos:
            result = buscar_tmdb_y_detalles(
                titulo,
                None,
                object_type,
                api_key
            )
            if result.get("tmdb_match"):
                return result

    return {
        "tmdb_id": None,
        "tmdb_title_es": None,
        "tmdb_cast": None,
        "tmdb_genres": None,
        "tmdb_overview_es": None,
        "tmdb_match": False,
    }

def aplicar_filtros(df, search, selected_type, unique_titles, only_tmdb):
    df_filtrado = df.copy()

    campo_busqueda = "title_display" if "title_display" in df_filtrado.columns else "title_final"

    if search:
        df_filtrado = df_filtrado[
            df_filtrado[campo_busqueda].astype(str).str.contains(search, case=False, na=False)
        ]

    if selected_type != "Todos" and "object_type" in df_filtrado.columns:
        df_filtrado = df_filtrado[df_filtrado["object_type"] == selected_type]

    if unique_titles:
        df_filtrado = df_filtrado.drop_duplicates(subset=[campo_busqueda])

    if only_tmdb and "tmdb_match" in df_filtrado.columns:
        df_filtrado = df_filtrado[df_filtrado["tmdb_match"] == True]

    return df_filtrado

def enriquecer_filtro_actual(df, api_key, search, selected_type, unique_titles, max_items=None):
    df = df.copy()

    subset = aplicar_filtros(
        df,
        search=search,
        selected_type=selected_type,
        unique_titles=unique_titles,
        only_tmdb=False
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

        progress.progress(i / total, text=f"Enriqueciendo filtro con TMDB... {i}/{total}")
        time.sleep(0.03)

    df["title_display"] = df["tmdb_title_es"].fillna(df["title_final"])

    return df, total

def convertir_a_csv(df):
    return df.to_csv(index=False).encode("utf-8-sig")

if "df_catalogo" not in st.session_state:
    st.session_state.df_catalogo = None

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

    st.write(f"Número de contenidos tras limpieza editorial: {len(df)}")

    st.subheader("Filtros")

    search = st.text_input("🔎 Buscar por título")

    object_types = []
    if "object_type" in df.columns:
        object_types = sorted(df["object_type"].dropna().unique().tolist())

    selected_type = st.selectbox("Tipo de contenido", ["Todos"] + object_types)

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
                        max_items=None
                    )
                    st.session_state.df_catalogo = df_actualizado
                    df = df_actualizado
                    st.success(f"Filtro enriquecido con TMDB ✅ ({n_enriquecidos} títulos procesados)")
                except Exception as e:
                    st.error(f"Error al enriquecer el filtro: {e}")

    df_filtrado = aplicar_filtros(df, search, selected_type, unique_titles, only_tmdb)

    columnas_mostrar = [
        col for col in [
            "original_title",
            "tmdb_title_es",
            "title_display",
            "object_type",
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
