---
language:
- en
- fr
- es
license: other
license_name: non-commercial
license_link: LICENSE
tags:
- text-to-image
- image-to-image
- diffusers
- qwen-image
- qwen-image-2.1
- lora
- adult-anatomy
- nsfw
- not-for-all-audiences
base_model: Qwen/Qwen-Image-2.1
pipeline_tag: text-to-image
---

<div style="display:flex; flex-wrap:nowrap; gap:6px; align-items:flex-start; overflow-x:auto;"><img alt="Visitors" src="https://visitor-badge.laobi.icu/badge?page_id=perpetual3x.Qwen-Image-Genitalia-Anatomy-Fix-NSFW-LoRA-v1" height="20" style="display:block; height:20px; width:auto; margin:0; padding:0; flex:none;"><img alt="Base model" src="https://img.shields.io/badge/Base-Qwen%20Image%202.1-blue" height="20" style="display:block; height:20px; width:auto; margin:0; padding:0; flex:none;"><img alt="GPU" src="https://img.shields.io/badge/GPU-RTX%20PRO%206000%20Blackwell%2096GB-blue" height="20" style="display:block; height:20px; width:auto; margin:0; padding:0; flex:none;"><img alt="Training" src="https://img.shields.io/badge/Training-16K%20Steps-purple" height="20" style="display:block; height:20px; width:auto; margin:0; padding:0; flex:none;"></div>

### Language / Langue / Idioma

**🇬🇧 English — displayed below**

-----------

<details name="language-version">
<summary><strong>🇫🇷 Français - afficher la version complète</strong></summary>

---------

# LoRA NSFW de correction de l'anatomie génitale Qwen Image v1

### Un seul adaptateur Qwen Image 2.1 pour l'anatomie adulte féminine et masculine : génération, édition et combinaison avec des LoRA compatibles.

![Couverture — puzzle des cinq branches anatomiques](assets/cover.png)

*La couverture est une illustration conceptuelle des cinq branches d'entraînement, pas une image générée par le modèle.*

Mes précédentes éditions 2512 traitaient séparément l'anatomie féminine dans plusieurs contextes visuels et l'anatomie masculine. J'ai cette fois réuni ces données anatomiques pour entraîner **un adaptateur Qwen Image 2.1**. Le but est d'améliorer la cohérence anatomique entre différents sujets adultes, pas de promettre qu'un seul token changera la nationalité d'une personne ou sauvera tous les angles de caméra impossibles.

On peut généralement commencer par une **description en langage naturel**. Les tokens d'entraînement ci-dessous sont aussi disponibles pour ceux qui souhaitent essayer un contrôle plus explicite. Ce sont des étiquettes de tiroirs, pas des agents de contrôle aux frontières : prononcer une étiquette plus fort ne garantit ni que le modèle ouvrira le bon tiroir, ni qu'il dessinera un bassin parfait. Comparez plusieurs seeds et gardez ce qui fonctionne réellement pour votre scène.

## Contenu de cette version

- **Un LoRA pour Qwen Image 2.1 :** [`Genitalia-Anatomy-2.1-v1.safetensors`](Genitalia-Anatomy-2.1-v1.safetensors). Ne le chargez pas dans Qwen Image 2512.
- **3 689 images d'entraînement** issues de mes précédents travaux anatomiques 2512 : anatomie féminine occidentale, chinoise, japonaise et coréenne, ainsi qu'anatomie masculine sans catégorie nationale particulière.
- Un déclencheur principal et cinq tokens de catégorie facultatifs. Le langage naturel reste le premier choix recommandé ; les tokens ne garantissent pas des résultats nettement séparés.
- Utilisation possible en **génération texte-vers-image et en édition conditionnée par une image** dans un flux Qwen Image 2.1 compatible, ainsi qu'en combinaison avec d'autres LoRA Qwen Image 2.1.
- Treize grands exemples générés, plus la couverture illustrée. **Leurs prompts ne sont pas fournis ici** ; des prompts de scène figurent dans mes fiches de modèles précédentes.
- Deux références d'inférence Qwen Image 2.1, dont une variante à VRAM réduite.

## Exemples — Qwen Image 2.1 + Viggle

J'ai essayé plusieurs méthodes d'accélération disponibles pour 2.1 et utilisé **Viggle** pour les exemples présentés ici. Ces images sont une sélection de résultats, pas un taux de réussite mesuré ni une comparaison contrôlée des accélérateurs. Aucun de leurs prompts n'est reproduit dans cette version.

### Exemple 1

![Exemple 1 — étude d'une femme adulte](assets/example01.png)

### Exemple 2

![Exemple 2 — étude d'une femme adulte](assets/example02.png)

### Exemple 3

![Exemple 3 — étude d'une femme adulte](assets/example03.png)

### Exemple 4

![Exemple 4 — étude d'une femme adulte](assets/example04.png)

### Exemple 5

![Exemple 5 — étude d'une femme adulte](assets/example05.png)

### Exemple 6

![Exemple 6 — étude d'une femme adulte](assets/example06.png)

### Exemple 7

![Exemple 7 — étude d'une femme adulte](assets/example07.png)

### Exemple 8

![Exemple 8 — étude d'un homme adulte](assets/example08.png)

### Exemple 9

![Exemple 9 — étude d'un homme adulte](assets/example09.png)

### Exemple 10

![Exemple 10 — étude d'un homme adulte](assets/example10.png)

### Exemple 11

![Exemple 11 — étude d'un homme adulte](assets/example11.png)

### Exemple 12

![Exemple 12 — étude d'un homme adulte](assets/example12.png)

### Exemple 13

![Exemple 13 — étude d'un homme adulte](assets/example13.png)

## Déclencheur principal et tokens de catégorie facultatifs

Les captions d'entraînement utilisaient un déclencheur commun et des tokens propres à chaque catégorie :

| Token | Association pendant l'entraînement |
|:---|:---|
| `zl_private_v1` | Déclencheur commun de cet adaptateur combiné |
| `zl_uniprivate_v1` | Sous-ensemble d'anatomie féminine occidentale |
| `zl_cnprivate_v1` | Sous-ensemble d'anatomie féminine chinoise |
| `zl_jpprivate_v1` | Sous-ensemble d'anatomie féminine japonaise |
| `zl_krprivate_v1` | Sous-ensemble d'anatomie féminine coréenne |
| `zl_maleprivate_v1` | Sous-ensemble d'anatomie masculine, sans nationalité particulière |

Commencez par décrire en langage courant la personne adulte, la pose, l'anatomie et la scène. Pour tester un token de catégorie, ajoutez le déclencheur commun et **un** token de catégorie pertinent, puis comparez avec la même seed sans ce token. Apprises au sein d'un seul LoRA, ces associations peuvent se chevaucher ; ni un token ni une étiquette d'origine ne garantissent une anatomie correcte. Ne les traitez pas comme des sélecteurs d'identité pour des personnes réelles.

## Détails de l'entraînement

| Paramètre | Valeur |
|:---|:---|
| Images d'entraînement | 3 689 |
| Modèle de base | Qwen Image 2.1 |
| GPU | NVIDIA RTX PRO 6000 Blackwell / 96 GB |
| Rank | 64 |
| Taille de lot | 8 |
| Étapes | 16 000 |
| Poids | `Genitalia-Anatomy-2.1-v1.safetensors` |

Il s'agit d'un **nouvel entraînement sur 2.1**, et non d'une fusion des fichiers de poids 2512 précédents. Les données d'entraînement ont été réunies ; les anciens adaptateurs restent destinés à leurs propres modèles de base.

## Génération, édition et combinaison de LoRA

Qwen Image 2.1 permet la génération texte-vers-image et l'édition conditionnée par une image grâce à son [pipeline Diffusers](https://huggingface.co/docs/diffusers/main/api/pipelines/qwenimage21). Cet adaptateur est destiné aux deux usages. Les deux scripts ci-dessous illustrent **la génération T2I** ; pour l'édition, utilisez un flux 2.1 compatible qui transmet une image d'entrée au pipeline avec cet adaptateur chargé. Les exemples de cette page ne constituent pas un banc d'essai séparé pour l'édition.

Il est également possible de **combiner** cet adaptateur à l'inférence avec d'autres LoRA conçus pour la **même architecture Qwen Image 2.1**, en leur donnant des noms distincts et des poids réglables. Voir le [guide Diffusers sur les LoRA multiples](https://huggingface.co/docs/diffusers/main/using-diffusers/merge_loras). Testez d'abord chaque adaptateur seul, puis comparez la combinaison à des intensités modérées : des concepts communs peuvent se renforcer ou se gêner. Il n'est pas nécessaire de fusionner définitivement les fichiers de poids, et la compatibilité avec les LoRA 2512 ne doit pas être supposée.

## Références d'inférence Qwen Image 2.1

Les exemples ont été générés avec un flux **Qwen Image 2.1 accéléré par Viggle**. Ces références adaptent à ce fichier de poids la méthode d'inférence 2.1 de mon [modèle d'anatomie d'Europe de l'Est v2](https://huggingface.co/perpetual3x/Eastern-European-Female-Anatomy-NSFW-LoRA-v2) :

| Flux | Référence |
|:---|:---|
| Qwen Image 2.1 + Viggle, VRAM plus élevée | [reference_21_viggle.py](reference_21_viggle.py) |
| Qwen Image 2.1 + Viggle, VRAM réduite (expérimental) | [reference_21_viggle_low_vram.py](reference_21_viggle_low_vram.py) |

Le script à VRAM réduite **nécessite toujours un GPU CUDA**. Il décharge les composants inactifs en RAM système ; il peut donc être plus lent et exiger beaucoup de mémoire hôte. Ces scripts sont des références, sans garantie de comportement identique avec chaque future version de Diffusers ou de l'accélérateur.

## Portée, utilisation responsable et licence

Cet adaptateur est réservé aux créations **licites et consenties concernant des sujets clairement adultes**. Il ne garantit pas une anatomie correcte pour chaque seed ou chaque point de vue difficile. Les occultations, poses extrêmes, angles inhabituels et combinaisons de tokens demandent toujours une vérification attentive.

Ne l'utilisez pas pour représenter des mineurs ou des personnes d'apparence juvénile, pour produire du contenu sexuel non consenti, ni pour sexualiser une personne réelle identifiable sans sa permission. Ne l'utilisez pas pour harceler, tromper ou enfreindre la loi et les règles des plateformes.

**Usage non commercial uniquement.** Consultez la [licence](LICENSE). Tout usage commercial ou toute redistribution nécessite l'autorisation écrite préalable de l'auteur.

Pour trouver des prompts de scène, consultez mes précédentes fiches de modèles d'anatomie [chinoise](https://huggingface.co/perpetual3x/Qwen-Image-Chinese-Female-Anatomy-Realism-NSFW-LoRA-v1), [japonaise](https://huggingface.co/perpetual3x/Qwen-Image-Japanese-Female-Anatomy-Realism-NSFW-LoRA-v1), [coréenne](https://huggingface.co/perpetual3x/Qwen-Image-Korean-Female-Anatomy-Realism-NSFW-LoRA-v1) et [masculine](https://huggingface.co/perpetual3x/Qwen-Image-Male-Anatomy-Realism-NSFW-LoRA-v1).

</details>

<br>


<details name="language-version">
<summary><strong>🌐 Español latinoamericano - mostrar la versión completa</strong></summary>

---------

# LoRA NSFW para corregir la anatomía genital en Qwen Image v1

### Un solo adaptador Qwen Image 2.1 para anatomía adulta femenina y masculina: generación, edición y combinación con LoRAs compatibles.

![Portada — rompecabezas de las cinco ramas anatómicas](assets/cover.png)

*La portada es una ilustración conceptual de las cinco ramas de entrenamiento; no es una imagen generada por el modelo.*

Mis ediciones anteriores para 2512 abordaban por separado la anatomía femenina en varios contextos visuales y la anatomía masculina. Esta vez reuní esos datos anatómicos y entrené **un adaptador para Qwen Image 2.1**. El objetivo es mejorar la coherencia anatómica en distintos sujetos adultos, no prometer que un solo token cambie la nacionalidad de una persona o salve todos los ángulos de cámara imposibles.

Por lo general, puedes comenzar con una **descripción en lenguaje natural**. También incluyo los tokens de entrenamiento para quien quiera probar un control más explícito. Son etiquetas de cajones, no agentes de migración: decir una etiqueta con más fuerza no garantiza que el modelo abra el cajón correcto, mucho menos que dibuje una pelvis perfecta. Compara varias semillas y conserva lo que realmente funcione para tu escena.

## Qué incluye esta versión

- **Un LoRA para Qwen Image 2.1:** [`Genitalia-Anatomy-2.1-v1.safetensors`](Genitalia-Anatomy-2.1-v1.safetensors). No lo cargues en Qwen Image 2512.
- **3.689 imágenes de entrenamiento** procedentes de mis trabajos anatómicos previos para 2512: anatomía femenina occidental, china, japonesa y coreana, y anatomía masculina sin una categoría nacional específica.
- Un trigger principal y cinco tokens de categoría opcionales. El lenguaje natural es el primer paso recomendado; los tokens no garantizan resultados claramente separados.
- Uso en **texto a imagen y edición condicionada por una imagen** dentro de un flujo Qwen Image 2.1 compatible, además de combinación con otros LoRAs de Qwen Image 2.1.
- Trece ejemplos generados en tamaño grande, además de la portada ilustrada. **Aquí no se incluyen sus prompts**; hay prompts de escenas en mis fichas de modelos anteriores.
- Dos referencias de inferencia para Qwen Image 2.1, incluida una variante de menor VRAM.

## Ejemplos — Qwen Image 2.1 + Viggle

Probé varios métodos de aceleración disponibles para 2.1 y utilicé **Viggle** para los ejemplos de esta página. Estas imágenes son una selección de resultados, no una tasa de éxito medida ni una comparación controlada entre aceleradores. No se reproduce ninguno de sus prompts en esta versión.

### Ejemplo 1

![Ejemplo 1 — estudio de una mujer adulta](assets/example01.png)

### Ejemplo 2

![Ejemplo 2 — estudio de una mujer adulta](assets/example02.png)

### Ejemplo 3

![Ejemplo 3 — estudio de una mujer adulta](assets/example03.png)

### Ejemplo 4

![Ejemplo 4 — estudio de una mujer adulta](assets/example04.png)

### Ejemplo 5

![Ejemplo 5 — estudio de una mujer adulta](assets/example05.png)

### Ejemplo 6

![Ejemplo 6 — estudio de una mujer adulta](assets/example06.png)

### Ejemplo 7

![Ejemplo 7 — estudio de una mujer adulta](assets/example07.png)

### Ejemplo 8

![Ejemplo 8 — estudio de un hombre adulto](assets/example08.png)

### Ejemplo 9

![Ejemplo 9 — estudio de un hombre adulto](assets/example09.png)

### Ejemplo 10

![Ejemplo 10 — estudio de un hombre adulto](assets/example10.png)

### Ejemplo 11

![Ejemplo 11 — estudio de un hombre adulto](assets/example11.png)

### Ejemplo 12

![Ejemplo 12 — estudio de un hombre adulto](assets/example12.png)

### Ejemplo 13

![Ejemplo 13 — estudio de un hombre adulto](assets/example13.png)

## Trigger principal y tokens de categoría opcionales

Las captions de entrenamiento emplearon un trigger común y tokens específicos para cada categoría:

| Token | Asociación durante el entrenamiento |
|:---|:---|
| `zl_private_v1` | Trigger común de este adaptador combinado |
| `zl_uniprivate_v1` | Subconjunto de anatomía femenina occidental |
| `zl_cnprivate_v1` | Subconjunto de anatomía femenina china |
| `zl_jpprivate_v1` | Subconjunto de anatomía femenina japonesa |
| `zl_krprivate_v1` | Subconjunto de anatomía femenina coreana |
| `zl_maleprivate_v1` | Subconjunto de anatomía masculina, sin nacionalidad específica |

Comienza describiendo en lenguaje corriente a la persona adulta, la pose, la anatomía y la escena. Si deseas probar un token de categoría, añade el trigger común y **un** token de categoría pertinente; luego compara con la misma semilla sin él. Estas asociaciones se aprendieron en un solo LoRA y pueden superponerse: ni un token ni una etiqueta de origen garantizan una anatomía correcta. No los uses como selectores de identidad de personas reales.

## Datos de entrenamiento

| Parámetro | Valor |
|:---|:---|
| Imágenes de entrenamiento | 3.689 |
| Modelo base | Qwen Image 2.1 |
| GPU | NVIDIA RTX PRO 6000 Blackwell / 96 GB |
| Rank | 64 |
| Tamaño de lote | 8 |
| Pasos | 16.000 |
| Archivo de pesos | `Genitalia-Anatomy-2.1-v1.safetensors` |

Este es un **nuevo entrenamiento sobre 2.1**, no una fusión de los archivos de pesos 2512 anteriores. Se reunieron los datos de entrenamiento; los adaptadores anteriores siguen destinados a sus respectivos modelos base.

## Generación, edición y combinación de LoRAs

Qwen Image 2.1 permite generar imágenes desde texto y editarlas con una imagen de entrada mediante su [pipeline de Diffusers](https://huggingface.co/docs/diffusers/main/api/pipelines/qwenimage21). Este adaptador está pensado para ambos usos. Los dos scripts siguientes muestran **T2I**; para editar, utiliza un flujo 2.1 compatible que pase una imagen de entrada al pipeline mientras este adaptador esté cargado. Los ejemplos de esta página no constituyen una evaluación independiente de la edición.

También puedes **combinar** este adaptador durante la inferencia con otros LoRAs creados para la **misma arquitectura Qwen Image 2.1**, asignándoles nombres distintos y pesos ajustables. Consulta la [guía de Diffusers para usar varios LoRAs](https://huggingface.co/docs/diffusers/main/using-diffusers/merge_loras). Primero prueba cada adaptador por separado; después compara la combinación con intensidades moderadas: los conceptos compartidos pueden reforzarse o competir entre sí. No hace falta fusionar permanentemente los archivos de pesos, y no debe suponerse compatibilidad con LoRAs para 2512.

## Referencias de inferencia para Qwen Image 2.1

Los ejemplos se generaron con un flujo **Qwen Image 2.1 acelerado por Viggle**. Estas referencias adaptan a este archivo de pesos el método de inferencia 2.1 de mi [modelo de anatomía de Europa del Este v2](https://huggingface.co/perpetual3x/Eastern-European-Female-Anatomy-NSFW-LoRA-v2):

| Flujo | Referencia |
|:---|:---|
| Qwen Image 2.1 + Viggle, mayor VRAM | [reference_21_viggle.py](reference_21_viggle.py) |
| Qwen Image 2.1 + Viggle, menor VRAM (experimental) | [reference_21_viggle_low_vram.py](reference_21_viggle_low_vram.py) |

El script de menor VRAM **todavía requiere una GPU CUDA**. Descarga los componentes inactivos a la RAM del sistema, por lo que puede ser más lento y necesitar bastante memoria del equipo. Son scripts de referencia, no una garantía de que todas las futuras versiones de Diffusers o del acelerador funcionen igual.

## Alcance, uso responsable y licencia

Este adaptador se destina a trabajos **legales y consentidos con sujetos claramente adultos**. No garantiza una anatomía correcta en todas las semillas o vistas difíciles. Las oclusiones, poses extremas, ángulos inusuales y combinaciones de tokens todavía requieren una revisión cuidadosa.

No lo utilices para representar a menores ni a personas de apariencia juvenil, crear contenido sexual no consentido o sexualizar a personas reales identificables sin permiso. No lo uses para acosar, engañar ni infringir las leyes o las normas de las plataformas.

**Solo uso no comercial.** Consulta la [licencia](LICENSE). El uso comercial o la redistribución requieren autorización previa por escrito del autor.

Para consultar prompts de escenas, revisa mis fichas anteriores de anatomía [china](https://huggingface.co/perpetual3x/Qwen-Image-Chinese-Female-Anatomy-Realism-NSFW-LoRA-v1), [japonesa](https://huggingface.co/perpetual3x/Qwen-Image-Japanese-Female-Anatomy-Realism-NSFW-LoRA-v1), [coreana](https://huggingface.co/perpetual3x/Qwen-Image-Korean-Female-Anatomy-Realism-NSFW-LoRA-v1) y [masculina](https://huggingface.co/perpetual3x/Qwen-Image-Male-Anatomy-Realism-NSFW-LoRA-v1).

</details>


-----


# Qwen Image Genitalia Anatomy Fix NSFW LoRA v1

### One Qwen Image 2.1 adapter for adult female and male anatomy, for T2I, editing, and compatible LoRA stacks.

![Cover — five-part anatomy series puzzle](assets/cover.png)

*The cover is a conceptual illustration of the five training branches, not a generated model sample.*

My earlier 2512 releases treated female anatomy in several visual contexts and male anatomy separately. This time I brought those anatomy-focused training sets together and trained **one adapter for Qwen Image 2.1**. The point is anatomical coherence across different adult subjects, not a promise that a single token can turn one person into a different nationality or rescue every impossible camera angle.

You can usually start with **natural-language prompting**. I also provide the training tokens below for anyone who wants more explicit control. Think of them as labels on drawers, not border-control officers: saying a label louder does not guarantee that the model will inspect the right passport, much less draw a perfect pelvis. Try them, compare several seeds, and keep the version that actually works for your scene.

## What this release contains

- **One Qwen Image 2.1 LoRA:** [`Genitalia-Anatomy-2.1-v1.safetensors`](Genitalia-Anatomy-2.1-v1.safetensors). Do not load it into Qwen Image 2512.
- **3,689 training images** drawn from my previous 2512 anatomy-focused work: Western female, Chinese female, Japanese female, Korean female, and male anatomy without a nationality-specific category.
- A main trigger and five optional category tokens. Natural language is the recommended first attempt; the tokens are not guaranteed to produce sharply separated results.
- Supports **text-to-image and image-conditioned editing** in a compatible Qwen Image 2.1 workflow, and can be stacked with other Qwen Image 2.1 LoRAs.
- Thirteen large generated examples, plus the illustrated cover. **No example prompts are supplied here**; scene prompts are available in my earlier model cards.
- Two Qwen Image 2.1 inference references, including a lower-VRAM variant.

## Examples — Qwen Image 2.1 + Viggle

I tested several available 2.1 acceleration approaches and used **Viggle** for the examples here. These images show selected outputs, not a measured success rate or a controlled accelerator comparison. None of the image prompts are reproduced in this release.

### Example 1

![Example 1 — adult female study](assets/example01.png)

### Example 2

![Example 2 — adult female study](assets/example02.png)

### Example 3

![Example 3 — adult female study](assets/example03.png)

### Example 4

![Example 4 — adult female study](assets/example04.png)

### Example 5

![Example 5 — adult female study](assets/example05.png)

### Example 6

![Example 6 — adult female study](assets/example06.png)

### Example 7

![Example 7 — adult female study](assets/example07.png)

### Example 8

![Example 8 — adult male study](assets/example08.png)

### Example 9

![Example 9 — adult male study](assets/example09.png)

### Example 10

![Example 10 — adult male study](assets/example10.png)

### Example 11

![Example 11 — adult male study](assets/example11.png)

### Example 12

![Example 12 — adult male study](assets/example12.png)

### Example 13

![Example 13 — adult male study](assets/example13.png)

## Main trigger and optional category tokens

The captions used a shared trigger and category-specific tokens:

| Token | Training association |
|:---|:---|
| `zl_private_v1` | Shared trigger for this combined adapter |
| `zl_uniprivate_v1` | Western female anatomy training subset |
| `zl_cnprivate_v1` | Chinese female anatomy training subset |
| `zl_jpprivate_v1` | Japanese female anatomy training subset |
| `zl_krprivate_v1` | Korean female anatomy training subset |
| `zl_maleprivate_v1` | Male anatomy training subset, not tied to a nationality |

Start by describing the adult person, pose, anatomy, and scene in ordinary language. If you want to test a category token, add the shared trigger and **one** relevant category token, then compare against the same seed without it. These associations were learned in one LoRA and may overlap; neither a token nor an ancestry label is an anatomical guarantee. Avoid treating the tokens as identity selectors for real people.

## Training details

| Parameter | Value |
|:---|:---|
| Training images | 3,689 |
| Base model | Qwen Image 2.1 |
| GPU | NVIDIA RTX PRO 6000 Blackwell / 96 GB |
| Rank | 64 |
| Batch size | 8 |
| Training steps | 16,000 |
| Weight | `Genitalia-Anatomy-2.1-v1.safetensors` |

This is a **new 2.1 training run**, not a file-level merge of the earlier 2512 weights. The training material was consolidated; the older adapters remain intended for their own base model.

## T2I, editing, and combining LoRAs

Qwen Image 2.1 supports both text-to-image generation and image-conditioned editing through its [Diffusers pipeline](https://huggingface.co/docs/diffusers/main/api/pipelines/qwenimage21). This adapter is intended for both workflows. The two scripts below demonstrate **T2I**; for editing, use a compatible 2.1 workflow that passes an input image to the pipeline while this adapter is loaded. The examples on this page are not a separate edit benchmark.

You can also **stack** this adapter at inference time with other LoRAs built for the **same Qwen Image 2.1 architecture**, using separate adapter names and adjustable weights. See the [Diffusers multiple-LoRA guide](https://huggingface.co/docs/diffusers/main/using-diffusers/merge_loras). Start by testing each adapter alone, then compare the stack at modest strengths: shared concepts can reinforce or compete with one another. Stacking does not require permanently merging weight files, and compatibility with 2512 LoRAs should not be assumed.

## Qwen Image 2.1 inference references

The examples were generated with a **Viggle-accelerated Qwen Image 2.1** workflow. These references adapt the 2.1 inference approach from my [Eastern European Anatomy v2](https://huggingface.co/perpetual3x/Eastern-European-Female-Anatomy-NSFW-LoRA-v2) to this weight:

| Workflow | Reference |
|:---|:---|
| Qwen Image 2.1 + Viggle, higher VRAM | [reference_21_viggle.py](reference_21_viggle.py) |
| Qwen Image 2.1 + Viggle, lower VRAM (experimental) | [reference_21_viggle_low_vram.py](reference_21_viggle_low_vram.py) |

The lower-VRAM script **still requires a CUDA GPU**. It offloads inactive components to system RAM, so it can be slower and may require substantial host memory. These are reference scripts, not a promise that every future Diffusers or accelerator revision will behave identically.

## Scope, responsible use, and license

This adapter is for **clearly adult, consensual, lawful** image work. It does not guarantee correct anatomy in every seed or difficult view. Occlusion, extreme poses, unusual camera angles, and combined tokens still need careful inspection.

Do not use it for minors or youthful-looking subjects, non-consensual sexual content, or sexualized depictions of identifiable real people without permission. Do not use it to harass, deceive, or violate applicable law or platform rules.

**Non-commercial use only.** See the [license](LICENSE). Commercial use or redistribution requires prior written permission from the author.

For scene-prompt references, see my earlier [Chinese](https://huggingface.co/perpetual3x/Qwen-Image-Chinese-Female-Anatomy-Realism-NSFW-LoRA-v1), [Japanese](https://huggingface.co/perpetual3x/Qwen-Image-Japanese-Female-Anatomy-Realism-NSFW-LoRA-v1), [Korean](https://huggingface.co/perpetual3x/Qwen-Image-Korean-Female-Anatomy-Realism-NSFW-LoRA-v1), and [male anatomy](https://huggingface.co/perpetual3x/Qwen-Image-Male-Anatomy-Realism-NSFW-LoRA-v1) model cards.
