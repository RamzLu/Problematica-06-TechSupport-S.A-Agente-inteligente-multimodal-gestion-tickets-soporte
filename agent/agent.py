import os

# LiteLLM necesita saber donde esta el servidor de Ollama (igual que en la clase de ADK).
os.environ["OLLAMA_API_BASE"] = "http://localhost:11434"

from google.adk.agents.llm_agent import Agent
from google.adk.models.lite_llm import LiteLlm

from .tools.tools import (
    actualizar_estado,
    adjuntar_archivo,
    agregar_comentario,
    asignar_responsable,
    buscar_tickets,
    cambiar_prioridad,
    consultar_historial,
    consultar_ticket,
    crear_ticket,
    generar_resumen,
)

# Modelo de IA: qwen3.5:4b (vision + tools, verificado con `ollama show`).
# reasoning_effort="None" desactiva el modo razonamiento, como en el codigo del docente
# (adk/agent/agent.py); el apunte de RAG indica que Qwen 3.5 da problemas con el.
# temperature=0 sigue la recomendacion del apunte de RAG (respuestas deterministas).
modelo = LiteLlm(
    "ollama_chat/qwen3.5:4b",
    reasoning_effort="None",
    temperature=0,
)

# Instrucciones provisorias: solo para comprobar que el agente arranca y responde.
# Se reemplazan en el Paso 7 por el rol, objetivo y restricciones definitivos.
root_agent = Agent(
    model=modelo,
    name="root_agent",
    description="Asistente para la gestion de tickets de soporte tecnico de TechSupport S.A.",
    instruction="Eres un asistente de soporte tecnico de TechSupport S.A. Responde en espanol.",
    tools=[
        crear_ticket,
        consultar_ticket,
        buscar_tickets,
        actualizar_estado,
        cambiar_prioridad,
        asignar_responsable,
        agregar_comentario,
        adjuntar_archivo,
        consultar_historial,
        generar_resumen,
    ],
)

