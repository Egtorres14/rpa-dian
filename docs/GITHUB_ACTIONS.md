# Evaluación desde GitHub

El repositorio es público durante el proceso de selección. La clave está
guardada como secreto de GitHub Actions en el entorno `evaluacion`, bajo el
nombre `TWOCAPTCHA_API_KEY`. El proyecto descargable incluye el flujo que la
utiliza, pero no contiene su valor. Para una ejecución local se necesita
una clave propia o la modalidad gratuita.

**Estado comprobado:** la demo de OCR completó diez archivos en el runner de
GitHub. La consulta real fue bloqueada por DIAN/Cloudflare antes de crear una
tarea CAPTCHA. La descarga remota se mantiene como opción experimental; la
entrega para evaluar la consulta y descarga usa la ejecución local.

## Para quien evalúa

1. Inicia sesión en GitHub y abre una
   [solicitud de prueba](https://github.com/Egtorres14/rpa-dian/issues/new?template=evaluacion.yml).
2. Elige una o diez facturas y publica la solicitud. No introduzcas credenciales.
3. La ejecución espera la aprobación de Gabriel. Tras la aprobación, revisa
   [Actions](https://github.com/Egtorres14/rpa-dian/actions/workflows/evaluacion.yml).
4. Si la ejecución termina correctamente, descarga **resultados** en la sección
   **Artifacts**. El ZIP interior contiene los PDF, CSV y métricas. Si DIAN
   bloquea el runner, la ejecución falla sin publicar un lote incompleto.

Leer un repositorio público no concede permiso para pulsar **Run workflow**.
El formulario permite solicitar la prueba sin recibir acceso de escritura.
Los resultados y registros de ejecución son públicos; los artefactos caducan
siete días después de su creación. La demo gratuita también se puede ejecutar
localmente con el comando del README.

## Para el propietario

En **Actions → Evaluación RPA DIAN → Run workflow**, selecciona la rama `main`:

- `demo`: OCR sobre las diez muestras, sin clave ni consumo de 2Captcha.
- `dian`: consulta y descarga nueva de una o diez facturas del lote incluido.

Una descarga queda esperando en **Review deployments**. Revisa quién solicitó
la prueba, la cantidad y el código de la ejecución; selecciona `evaluacion` y
aprueba para continuar. Las solicitudes públicas no pueden gastar saldo antes
de esa aprobación. Se permite aprobar las ejecuciones propias.

La configuración del entorno se administra en
[Settings → Environments → evaluacion](https://github.com/Egtorres14/rpa-dian/settings/environments).
El revisor requerido es `Egtorres14` y la única rama permitida es `main`. Para
cambiar la clave, actualiza el secreto dentro de ese entorno; no lo copies al
README, `.env.example`, Issues ni variables visibles.

## Límites y resultados

El flujo descarga los CUFE de `cufes.json`, sin aceptar URLs ni código del
solicitante. Usa un intento por factura, un máximo de dos tareas CAPTCHA por
factura y una sola ejecución simultánea. Una solicitud nueva puede sustituir
otra pendiente en la cola de concurrencia; se puede volver a solicitarla.
No hay una cuota global automática: cada aprobación autoriza un nuevo consumo.

La clave sólo se entrega al paso de descarga. Las acciones están fijadas a
commits y el token de GitHub tiene permiso de lectura de código. No se ejecutan
pull requests con el secreto. Los archivos HTML de diagnóstico y las sesiones
del navegador no forman parte del artefacto.

Un lote con errores o facturas omitidas falla. Un lote completo con estado
`REVISAR` publica los resultados y muestra una advertencia de calidad; esto
permite conservar el código de producto vacío de `HTFE-6599`, que ya falta en
el original. Revisa el detalle antes de dar por buenos todos los campos.

El resumen muestra el tiempo del robot y OCR, las tareas CAPTCHA y el coste
reportado. Excluye instalación, aprobación y espera en cola. El rendimiento del
runner de GitHub puede diferir del equipo donde se tomaron las mediciones del
informe, y la disponibilidad de DIAN o 2Captcha puede afectar una descarga.

La [demo comprobada](https://github.com/Egtorres14/rpa-dian/actions/runs/37097879155)
empleó 53,666 s de RPA/OCR: diez PDF y CSV, 21 productos, siete `OK`, tres
`REVISAR`, sin errores ni tareas de pago. Además del código ausente en la fuente,
el OCR produjo discrepancias en `SFA-556956` y `HISF-277602` (por ejemplo,
`S19911` se leyó como `$19911`). Se conservaron para revisión sin sustituirlos
por la capa nativa del PDF. Son resultados de Ubuntu, distintos de la medición
original en Windows.

La [consulta de una factura](https://github.com/Egtorres14/rpa-dian/actions/runs/37098192997)
falló por bloqueo del sitio tras 1,552 s, con cero PDF y cero tareas CAPTCHA.
La clave y el saldo se habían verificado correctamente. Resolver el widget
Turnstile no garantiza acceso desde una IP bloqueada por el sitio.

## Cuando termine el proceso

Primero cancela las ejecuciones pendientes y desactiva el flujo. Elimina el
secreto del entorno si ya no se va a usar. Después cambia la visibilidad a
privada en **Settings → General → Danger Zone → Change repository visibility**.
También se puede hacer con GitHub CLI:

```powershell
gh workflow disable evaluacion.yml --repo Egtorres14/rpa-dian
gh secret delete TWOCAPTCHA_API_KEY --env evaluacion --repo Egtorres14/rpa-dian
gh repo edit Egtorres14/rpa-dian --visibility private --accept-visibility-change-consequences
```

El flujo de pago comprueba además que el repositorio sea público. Al volverlo
privado no continuará aceptando descargas por este mecanismo. Las protecciones
de entorno disponibles cambian según el plan y la visibilidad; hay que revisarlas
si se desea habilitar otra vez. No se ha programado un cambio automático de fecha.
Las copias descargadas y los forks públicos existentes pueden permanecer públicos.

Referencias: [ejecución manual](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow),
[entornos y aprobaciones](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments),
[visibilidad](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility).
