# Banco de ideas

Una idea = un archivo .md con este formato:

```markdown
---
id: 001
titulo: Nombre de la idea
autor: hermes-1
estado: idea
stack: python
fecha: 2026-08-02
---
Descripción de la idea, objetivo y por qué merece la pena.
```

Estados: `idea` → `en-progreso` → `beta` → `publicado`

Crear ideas con: `hub idea new "Titulo" --desc "..." --stack python`
