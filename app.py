# app.py - Terraform AI Generator v2
# Nouvelles fonctionnalités : Voice + Modèles Azure prédéfinis + Export draw.io XML fidèle

import re
import streamlit as st
from PIL import Image
from utils.gemini import analyser_image_architecture
from utils.groq import generate_terraform
from utils.azure_devops import deployer_pipeline
from utils.voice import transcrire_audio
from utils.azure_templates import get_template_names, get_template

# ---------------------------------------------------------------------------
# Fonction : Terraform HCL → draw.io XML fidèle à l'architecture
# ---------------------------------------------------------------------------

def extraire_ressources(terraform_code: str) -> dict:
    """
    Extrait toutes les ressources et leurs attributs du code Terraform.
    Retourne un dict : { "type.nom" : { attributs } }
    """
    ressources = {}
    pattern = re.finditer(
        r'resource\s+"([^"]+)"\s+"([^"]+)"\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}',
        terraform_code,
        re.DOTALL,
    )
    for match in pattern:
        res_type = match.group(1)
        res_name = match.group(2)
        body     = match.group(3)
        cle      = f"{res_type}.{res_name}"
        ressources[cle] = {"type": res_type, "name": res_name, "body": body}
    return ressources


def extraire_dependances(ressources: dict) -> list:
    """
    Détecte les références entre ressources (ex: azurerm_lb.main.id)
    et retourne une liste de tuples (source_cle, cible_cle).
    """
    deps = []
    for cle, info in ressources.items():
        body = info["body"]
        refs = re.findall(r'(azurerm_\w+|kubernetes_\w+)\.(\w+)\.', body)
        for ref_type, ref_name in refs:
            ref_cle = f"{ref_type}.{ref_name}"
            if ref_cle in ressources and ref_cle != cle:
                deps.append((cle, ref_cle))
    return deps


def detecter_groupes(ressources: dict) -> dict:
    """
    Regroupe les ressources par famille logique pour reproduire
    la hiérarchie visuelle de l'architecture.
    """
    groupes = {
        "Infrastructure reseau":  [],
        "Load Balancer":          [],
        "AKS Cluster":            [],
        "Namespace frontend":     [],
        "Namespace backend":      [],
        "Namespace utility":      [],
        "Donnees":                [],
        "Securite":               [],
        "Autres":                 [],
    }

    for cle, info in ressources.items():
        t = info["type"]
        n = info["name"].lower()

        if t in ("azurerm_resource_group", "azurerm_virtual_network", "azurerm_subnet"):
            groupes["Infrastructure reseau"].append(cle)
        elif t in ("azurerm_lb", "azurerm_public_ip",
                   "azurerm_lb_backend_address_pool",
                   "azurerm_lb_rule",
                   "azurerm_lb_frontend_ip_configuration"):
            groupes["Load Balancer"].append(cle)
        elif t == "azurerm_kubernetes_cluster":
            groupes["AKS Cluster"].append(cle)
        elif t == "kubernetes_namespace":
            if "front" in n:
                groupes["Namespace frontend"].append(cle)
            elif "back" in n:
                groupes["Namespace backend"].append(cle)
            else:
                groupes["Namespace utility"].append(cle)
        elif t in ("kubernetes_service", "kubernetes_deployment"):
            if "front" in n or "nginx" in n or "flask" in n:
                groupes["Namespace frontend"].append(cle)
            elif "auth" in n or "api" in n:
                groupes["Namespace backend"].append(cle)
            elif "prometheus" in n or "grafana" in n or "elastic" in n:
                groupes["Namespace utility"].append(cle)
            else:
                groupes["Namespace backend"].append(cle)
        elif t in ("azurerm_mysql_server", "azurerm_sql_server",
                   "azurerm_sql_database", "azurerm_mysql_database",
                   "azurerm_storage_account", "azurerm_postgresql_server"):
            groupes["Donnees"].append(cle)
        elif t in ("azurerm_key_vault", "azurerm_network_security_group",
                   "azurerm_bastion_host"):
            groupes["Securite"].append(cle)
        else:
            groupes["Autres"].append(cle)

    return {k: v for k, v in groupes.items() if v}


# Styles visuels par type de ressource
STYLE_RESSOURCE = {
    "azurerm_resource_group":               ("#0078D4", "#ffffff", "Resource Group"),
    "azurerm_virtual_network":              ("#0072C6", "#ffffff", "Virtual Network"),
    "azurerm_subnet":                       ("#005BA1", "#ffffff", "Subnet"),
    "azurerm_lb":                           ("#00B7C3", "#ffffff", "Load Balancer"),
    "azurerm_public_ip":                    ("#50E6FF", "#003040", "Public IP"),
    "azurerm_lb_backend_address_pool":      ("#00B7C3", "#ffffff", "Backend Pool"),
    "azurerm_lb_rule":                      ("#00B7C3", "#ffffff", "LB Rule"),
    "azurerm_lb_frontend_ip_configuration": ("#00B7C3", "#ffffff", "Frontend IP"),
    "azurerm_kubernetes_cluster":           ("#326CE5", "#ffffff", "AKS Cluster"),
    "kubernetes_namespace":                 ("#2756C5", "#ffffff", "Namespace"),
    "kubernetes_service":                   ("#3E8EDE", "#ffffff", "K8s Service"),
    "kubernetes_deployment":                ("#1A3FA0", "#ffffff", "K8s Deployment"),
    "azurerm_mysql_server":                 ("#FF6D00", "#ffffff", "MySQL Server"),
    "azurerm_sql_server":                   ("#8E44AD", "#ffffff", "SQL Server"),
    "azurerm_sql_database":                 ("#9B59B6", "#ffffff", "SQL Database"),
    "azurerm_storage_account":              ("#F39C12", "#ffffff", "Storage Account"),
    "azurerm_key_vault":                    ("#E67E22", "#ffffff", "Key Vault"),
    "azurerm_bastion_host":                 ("#C0392B", "#ffffff", "Bastion Host"),
    "azurerm_network_security_group":       ("#E74C3C", "#ffffff", "NSG"),
    "azurerm_container_registry":           ("#2980B9", "#ffffff", "Container Registry"),
    "azurerm_app_service":                  ("#27AE60", "#ffffff", "App Service"),
    "azurerm_app_service_plan":             ("#2ECC71", "#ffffff", "App Service Plan"),
}

STYLE_DEFAULT = ("#95A5A6", "#ffffff")

GROUPE_STYLE = {
    "Infrastructure reseau":  ("#0D2137", "#50E6FF"),
    "Load Balancer":          ("#0D2137", "#00B7C3"),
    "AKS Cluster":            ("#1A1F5E", "#326CE5"),
    "Namespace frontend":     ("#1E3A5F", "#3E8EDE"),
    "Namespace backend":      ("#3D1A00", "#FF6D00"),
    "Namespace utility":      ("#1A3D1A", "#27AE60"),
    "Donnees":                ("#3D2200", "#F39C12"),
    "Securite":               ("#3D0000", "#E74C3C"),
    "Autres":                 ("#1A1A1A", "#95A5A6"),
}

GROUPE_LABELS = {
    "Infrastructure reseau": "Infrastructure Reseau",
    "Load Balancer":         "Load Balancer",
    "AKS Cluster":           "AKS Cluster",
    "Namespace frontend":    "Namespace : frontend",
    "Namespace backend":     "Namespace : backend",
    "Namespace utility":     "Namespace : utility",
    "Donnees":               "Donnees",
    "Securite":              "Securite",
    "Autres":                "Autres",
}


def terraform_to_drawio_xml(terraform_code: str) -> str:
    """
    Convertit du code Terraform HCL en XML draw.io fidèle à l'architecture.
    - Regroupe les ressources par famille logique (VNet, AKS, Namespaces...)
    - Dessine les conteneurs hiérarchiques (swimlanes)
    - Trace les connexions entre ressources dépendantes
    """
    ressources = extraire_ressources(terraform_code)
    if not ressources:
        return _build_xml_final(
            [_cell_simple(10, "Aucune ressource Terraform trouvee",
                          200, 200, 200, 60, "#95A5A6", "#ffffff")],
            []
        )

    dependances = extraire_dependances(ressources)
    groupes     = detecter_groupes(ressources)

    cells    = []
    edges    = []
    cell_id  = 10
    id_map   = {}

    # Constantes de mise en page
    MARGE        = 20
    NOEUD_W      = 160
    NOEUD_H      = 55
    H_GAP        = 20
    V_GAP        = 16
    COLS         = 3
    HEADER_H     = 36
    CONT_GAP     = 40

    def placer_groupe(nom_groupe, items, x0, y0):
        nonlocal cell_id

        nb   = len(items)
        cols = min(COLS, nb)
        rows = (nb + cols - 1) // cols

        inner_w = cols * NOEUD_W + (cols - 1) * H_GAP
        inner_h = rows * NOEUD_H  + (rows - 1) * V_GAP
        cont_w  = inner_w + 2 * MARGE
        cont_h  = inner_h + 2 * MARGE + HEADER_H

        bg_fill, bg_stroke = GROUPE_STYLE.get(nom_groupe, ("#1A1A2E", "#0078D4"))
        label_affiche      = GROUPE_LABELS.get(nom_groupe, nom_groupe)

        cont_id = cell_id
        cell_id += 1

        style_cont = (
            f"swimlane;startSize={HEADER_H};fillColor={bg_fill};"
            f"strokeColor={bg_stroke};strokeWidth=2;"
            f"fontColor={bg_stroke};fontSize=12;fontStyle=1;"
            f"rounded=1;arcSize=4;swimlaneLine=1;"
        )
        cells.append(
            f'    <mxCell id="{cont_id}" value="{label_affiche}" '
            f'style="{style_cont}" vertex="1" parent="1">\n'
            f'      <mxGeometry x="{x0}" y="{y0}" '
            f'width="{cont_w}" height="{cont_h}" as="geometry"/>\n'
            f'    </mxCell>'
        )

        for idx, cle in enumerate(items):
            info = ressources[cle]
            t, n = info["type"], info["name"]

            entry = STYLE_RESSOURCE.get(t)
            if entry:
                fill, font, lbl_prefix = entry
            else:
                fill, font = STYLE_DEFAULT
                lbl_prefix = (t.replace("azurerm_", "")
                               .replace("kubernetes_", "")
                               .replace("_", " ").title())

            col = idx % cols
            row = idx // cols
            nx  = MARGE + col * (NOEUD_W + H_GAP)
            ny  = HEADER_H + MARGE + row * (NOEUD_H + V_GAP)

            label = f"{lbl_prefix}&#xa;{n}"
            nid   = cell_id
            cell_id += 1
            id_map[cle] = nid

            style_node = (
                f"rounded=1;arcSize=8;whiteSpace=wrap;html=1;"
                f"fillColor={fill};fontColor={font};"
                f"strokeColor=#ffffff;strokeWidth=1;"
                f"fontSize=10;fontStyle=1;align=center;verticalAlign=middle;"
            )
            cells.append(
                f'    <mxCell id="{nid}" value="{label}" '
                f'style="{style_node}" vertex="1" parent="{cont_id}">\n'
                f'      <mxGeometry x="{nx}" y="{ny}" '
                f'width="{NOEUD_W}" height="{NOEUD_H}" as="geometry"/>\n'
                f'    </mxCell>'
            )

        return cont_w, cont_h

    # Disposition : colonne gauche / colonne droite
    col_gauche = ["Infrastructure reseau", "Load Balancer", "Donnees", "Securite", "Autres"]
    col_droite = ["AKS Cluster", "Namespace frontend", "Namespace backend", "Namespace utility"]

    y_g = 40
    larg_g = 0
    for nom in col_gauche:
        if nom not in groupes:
            continue
        w, h  = placer_groupe(nom, groupes[nom], 40, y_g)
        larg_g = max(larg_g, w)
        y_g   += h + CONT_GAP

    x_d = 40 + larg_g + CONT_GAP * 2
    y_d = 40
    for nom in col_droite:
        if nom not in groupes:
            continue
        w, h = placer_groupe(nom, groupes[nom], x_d, y_d)
        y_d += h + CONT_GAP

    # Arêtes
    eid = cell_id
    for (src, tgt) in set(dependances):
        sid = id_map.get(src)
        tid = id_map.get(tgt)
        if sid and tid:
            style_edge = (
                "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;"
                "jettySize=auto;exitX=1;exitY=0.5;exitDx=0;exitDy=0;"
                "entryX=0;entryY=0.5;entryDx=0;entryDy=0;"
                "strokeColor=#50E6FF;strokeWidth=1.5;fillColor=none;"
                "endArrow=block;endFill=1;"
            )
            edges.append(
                f'    <mxCell id="{eid}" style="{style_edge}" '
                f'edge="1" source="{sid}" target="{tid}" parent="1">\n'
                f'      <mxGeometry relative="1" as="geometry"/>\n'
                f'    </mxCell>'
            )
            eid += 1

    return _build_xml_final(cells, edges)


def _cell_simple(cid, label, x, y, w, h, fill, font):
    style = (
        f"rounded=1;arcSize=8;whiteSpace=wrap;html=1;"
        f"fillColor={fill};fontColor={font};"
        f"strokeColor=#ffffff;strokeWidth=1;"
        f"fontSize=10;fontStyle=1;align=center;verticalAlign=middle;"
    )
    return (
        f'    <mxCell id="{cid}" value="{label}" style="{style}" '
        f'vertex="1" parent="1">\n'
        f'      <mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/>\n'
        f'    </mxCell>'
    )


def _build_xml_final(cells: list, edges: list) -> str:
    contenu = "\n".join(cells + edges)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="app.diagrams.net" modified="" agent="Terraform AI Generator" version="21.0.0">
  <diagram name="Architecture Azure" id="terraform-arch">
    <mxGraphModel dx="1422" dy="762" grid="1" gridSize="10"
                  guides="1" tooltips="1" connect="1" arrows="1"
                  fold="1" page="1" pageScale="1"
                  pageWidth="1654" pageHeight="1169"
                  math="0" shadow="0">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
{contenu}
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""


# ---------------------------------------------------------------------------
# Configuration Streamlit
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Terraform AI Generator",
    page_icon="🚀",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
for key in ["analyse", "code_terraform", "deploiement_lance", "transcription"]:
    if key not in st.session_state:
        st.session_state[key] = None
if "deploiement_lance" not in st.session_state:
    st.session_state.deploiement_lance = False

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Configuration")
    st.info(
        "Variables requises dans `.env` :\n\n"
        "- `GEMINI_API_KEY`\n"
        "- `GROQ_API_KEY`\n"
        "- `AZURE_PAT`\n"
        "- `AZURE_ORG`\n"
        "- `AZURE_PROJECT`\n"
        "- `AZURE_REPO`"
    )
    st.divider()
    st.markdown("**Modèles utilisés**")
    st.markdown("- 🔵 Gemini 2.5 Flash (analyse image)")
    st.markdown("- 🟢 Whisper Large v3 (transcription voix)")
    st.markdown("- 🟢 LLaMA 3.3 70B (génération HCL)")

# ---------------------------------------------------------------------------
# Titre
# ---------------------------------------------------------------------------
st.title("🚀 Terraform AI Generator")
st.caption("Générez du code Terraform depuis une image, votre voix ou un modèle prédéfini.")

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab1, tab2, tab3 = st.tabs([
    "📁 Image d'architecture",
    "🎙️ Description vocale",
    "📋 Modèles Azure prédéfinis",
])

# ===========================================================================
# TAB 1 — Image
# ===========================================================================
with tab1:
    st.subheader("📁 Analysez un schéma d'architecture cloud")

    image_upload = st.file_uploader(
        "Téléversez votre schéma",
        type=["png", "jpg", "jpeg"],
        key="image_uploader",
    )

    if image_upload:
        image = Image.open(image_upload)
        st.image(image, caption="Schéma chargé", use_column_width=True)
        st.success("✅ Schéma chargé")

        extension = image_upload.name.split(".")[-1].lower()
        mime_type = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
        }.get(extension, "image/png")

        if st.button("🔍 Analyser et Générer", type="primary", key="btn_image"):
            with st.spinner("🔍 Vérification de l'image..."):
                try:
                    st.session_state.analyse = analyser_image_architecture(
                        image_bytes=image_upload.getvalue(),
                        mime_type=mime_type,
                    )
                except ValueError as e:
                    erreur = str(e)
                    if "IMAGE_NON_VALIDE" in erreur:
                        raison = erreur.replace("IMAGE_NON_VALIDE:", "")
                        st.warning("⚠️ Cette image ne représente pas une architecture cloud.")
                        st.error(f"**Raison détectée :** {raison}")
                        st.info(
                            "💡 **Conseils :** Téléversez un schéma d'architecture cloud comme :\n\n"
                            "- Un diagramme AWS / Azure / GCP\n"
                            "- Un schéma réseau avec des services cloud\n"
                            "- Un diagramme de déploiement ou de microservices\n\n"
                            "🔄 Veuillez changer d'image ci-dessus."
                        )
                    else:
                        st.error(f"❌ Erreur Gemini : {e}")
                    st.stop()
                except Exception as e:
                    st.error(f"❌ Erreur Gemini : {e}")
                    st.stop()

            with st.spinner("⚙️ Génération Terraform..."):
                try:
                    st.session_state.code_terraform = generate_terraform(st.session_state.analyse)
                    st.session_state.deploiement_lance = False
                except Exception as e:
                    st.error(f"❌ Erreur Groq : {e}")
                    st.stop()

# ===========================================================================
# TAB 2 — Voice
# ===========================================================================
with tab2:
    st.subheader("🎙️ Décrivez votre architecture par la voix")
    st.info(
        "Enregistrez votre voix en décrivant l'architecture souhaitée. "
        "Exemple : *'Je veux deux machines virtuelles Ubuntu derrière un load balancer avec une base de données SQL'*"
    )

    audio_file = st.audio_input("🎤 Enregistrez votre description", key="audio_recorder")

    if audio_file:
        st.audio(audio_file)

        if st.button("🎙️ Transcrire et Générer", type="primary", key="btn_voice"):
            with st.spinner("🎙️ Transcription Whisper..."):
                try:
                    st.session_state.transcription = transcrire_audio(
                        audio_bytes=audio_file.getvalue(),
                        filename="audio.wav",
                    )
                except Exception as e:
                    st.error(f"❌ Erreur transcription : {e}")
                    st.stop()

            st.subheader("📝 Transcription")
            st.info(st.session_state.transcription)

            with st.spinner("⚙️ Génération Terraform depuis la description vocale..."):
                try:
                    prompt_voice = f"""
L'utilisateur a décrit verbalement l'architecture suivante :
\"{st.session_state.transcription}\"

Génère le code Terraform Azure (provider azurerm) correspondant à cette description.
Inclus toutes les ressources nécessaires avec des variables et des outputs.
"""
                    st.session_state.analyse = st.session_state.transcription
                    st.session_state.code_terraform = generate_terraform(prompt_voice)
                    st.session_state.deploiement_lance = False
                except Exception as e:
                    st.error(f"❌ Erreur génération : {e}")
                    st.stop()

# ===========================================================================
# TAB 3 — Modèles Azure prédéfinis
# ===========================================================================
with tab3:
    st.subheader("📋 Choisissez un modèle d'infrastructure Azure")

    template_names = get_template_names()
    selected = st.selectbox("Sélectionnez un modèle :", template_names, key="template_select")

    if selected:
        template = get_template(selected)
        st.markdown(f"**Description :** {template['description']}")
        st.markdown("**Composants inclus :**")
        for comp in template["composants"]:
            st.markdown(f"- {comp}")

        if st.button("📋 Utiliser ce modèle", type="primary", key="btn_template"):
            st.session_state.code_terraform = template["code"]
            st.session_state.analyse = f"Modèle prédéfini : {selected}"
            st.session_state.deploiement_lance = False
            st.success(f"✅ Modèle **{selected}** chargé !")

        st.divider()
        st.markdown("**🤖 Personnaliser avec l'IA**")
        personalisation = st.text_area(
            "Décrivez vos modifications :",
            placeholder="Ex: Ajoute un deuxième subnet, change la région en East US, ajoute des tags...",
            key="personalisation_input",
        )

        if personalisation and st.button("✨ Personnaliser", key="btn_personnaliser"):
            with st.spinner("⚙️ Personnalisation en cours..."):
                try:
                    prompt_custom = f"""
Voici un code Terraform Azure de base pour {selected} :
{template['code']}

L'utilisateur souhaite les modifications suivantes :
{personalisation}

Génère le code Terraform modifié en intégrant ces changements.
"""
                    st.session_state.code_terraform = generate_terraform(prompt_custom)
                    st.session_state.analyse = f"Modèle {selected} personnalisé"
                    st.session_state.deploiement_lance = False
                except Exception as e:
                    st.error(f"❌ Erreur : {e}")

# ===========================================================================
# Résultats communs aux 3 tabs
# ===========================================================================
if st.session_state.analyse and st.session_state.code_terraform:
    st.divider()

    if st.session_state.analyse not in get_template_names():
        with st.expander("📋 Analyse / Description", expanded=False):
            st.markdown(st.session_state.analyse)

    st.subheader("📄 Code Terraform Généré")
    st.code(st.session_state.code_terraform, language="hcl")

    col1, col2 = st.columns(2)

    with col1:
        st.download_button(
            label="⬇️ Télécharger main.tf",
            data=st.session_state.code_terraform,
            file_name="main.tf",
            mime="text/plain",
            use_container_width=True,
        )

    with col2:
        try:
            drawio_xml = terraform_to_drawio_xml(st.session_state.code_terraform)
            st.download_button(
                label="🗺️ Télécharger diagramme draw.io",
                data=drawio_xml,
                file_name="architecture.drawio",
                mime="application/xml",
                use_container_width=True,
            )
        except Exception as e:
            st.error(f"❌ Erreur génération draw.io : {e}")

    st.divider()

    if not st.session_state.deploiement_lance:
        if st.button("📦 Déployer via Azure DevOps", use_container_width=True):
            with st.spinner("🔄 Push vers Azure DevOps..."):
                try:
                    resultat = deployer_pipeline(
                        content=st.session_state.code_terraform,
                        file_path="main.tf",
                        message=f"Terraform généré — {st.session_state.analyse[:50]}",
                    )
                    st.session_state.deploiement_lance = True
                    st.success(f"✅ Fichier poussé : {resultat}")
                except Exception as e:
                    st.error(f"❌ Erreur Azure DevOps : {e}")
    else:
        st.success("✅ Déjà poussé sur Azure DevOps pour cette génération.")

else:
    st.divider()
    st.info("👆 Choisissez un mode d'entrée ci-dessus pour générer votre code Terraform.")