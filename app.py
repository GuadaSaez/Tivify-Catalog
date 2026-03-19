import streamlit as st
import requests
import pandas as pd
import time

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

def preparar_dataframe(data):
    contents = data.get("contents", [])
    df = pd.DataFrame(contents)

    if df.empty:
        return df

    # Título desde el JSON si existe
    if "localized_titles" in df.columns:
        df["title_es"] = df["localized_titles"].apply(get_spanish_title)
    else:
        df["title_es"] = None

    # Título base
    if "original_title" in df.columns:
        df["title_final"] = df["title_es"].fillna(df["original_title"])
    else:
        df["title_final"] = df["title_es"]

    # Solo contenido editorial útil
    if "object_type" in df.columns:
        df = df[df["object_type"].isin(["movie", "show"])]

    # Columnas TMDB
    if "tmdb_title_es" not in df.columns:
        df["tmdb_title_es"] = None

    if "tmdb_match" not in df.columns:
        df["tmdb_match"] = False

    if "title_display" not in df.columns:
        df["title_display"] = df["title_final"]

    return df

@st.cache_data(show_spinner=False)
def buscar_tmdb_titulo_es(title, year, object_type, api_key):
    if not api_key or not title or not object_type:
        return {"tmdb_title_es": None, "tmdb_match": False}

    endpoint = "movie" if object_type == "movie" else "tv"
    url = f"https://api.themoviedb.org/3/search/{endpoint}"

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
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        results = response.json().get("results", [])

        if not results:
            return {"tmdb_title_es": None, "tmdb_match": False}

        best = results[0]
        tmdb_title_es = best.get("title") or best.get("name")

        return {
            "tmdb_title_es": tmdb_title_es,
            "tmdb_match": True
        }

    except Exception:
        return {"tmdb_title_es": None, "tmdb_match": False}

def aplicar_filtros(df, search, selected_type, unique_titles, only_tmdb):
    df_filtrado = df.copy()

    if search:
        campo_busqueda = "title_display" if "title_display" in df_filtrado.columns else "title_final"
        df_filtrado = df_filtrado[
            df_filtrado[campo_busqueda].astype(str).str.contains(search, case=False, na=False)
        ]

    if selected_type != "Todos":
        df_filtrado = df_filtrado[df_filtrado["object_type"] == selected_type]

    if unique_titles:
        campo_unico = "title_display" if "title_display" in df_filtrado.columns else "title_final"
        df_filtrado = df_filtrado.drop_duplicates(subset=[campo_unico])

    if only_tmdb:
        df_filtrado = df_filtrado[df_filtrado["tmdb_match"] == True]

    return df_filtrado

def enriquecer_filtro_actual(df, api_key, search, selected_type, unique_titles, max_items=None):
    df = df.copy()

    # Importante: aquí NO aplicamos only_tmdb=True
    subset = aplicar_filtros(
        df,
        search=search,
        selected_type=selected_type,
        unique_titles=unique_titles,
        only_tmdb=False
    )

    # Solo los que no tengan todavía TMDB
    subset = subset[subset["tmdb_match"] != True]

    if max_items is not None:
        subset = subset.head(max_items)

    total = len(subset)

    if total == 0:
        return df, 0

    progress = st.progress(0, text="Enriqueciendo filtro con TMDB...")

    for i, (idx, row) in enumerate(subset.iterrows(), start=1):
        result = buscar_tmdb_titulo_es(
            row.get("original_title"),
            row.get("release_year"),
            row.get("object_type"),
            api_key
        )

        df.at[idx, "tmdb_title_es"] = result["tmdb_title_es"]
        df.at[idx, "tmdb_match"] = result["tmdb_match"]

        progress.progress(i / total, text=f"Enriqueciendo filtro con TMDB... {i}/{total}")
        time.sleep(0.03)

    df["title_display"] = df["tmdb_title_es"].fillna(df["title_final"])

    return df, total

def convertir_a_csv(df):
    return df.to_csv(index=False).encode("utf-8-sig")

# Estado
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
            "id",
            "original_title",
            "title_es",
            "tmdb_title_es",
            "title_display",
            "object_type",
            "release_year",
            "runtime",
            "show_id",
            "tmdb_match"
        ] if col in df_filtrado.columns
    ]

    st.subheader("Resultados")
    st.write(f"Resultados encontrados: {len(df_filtrado)}")
    st.dataframe(df_filtrado[columnas_mostrar], use_container_width=True)

    csv_data = convertir_a_csv(df_filtrado)

    st.download_button(
        label="⬇️ Descargar resultados filtrados en CSV",
        data=csv_data,
        file_name="catalogo_filtrado_enriquecido.csv",
        mime="text/csv"
    )