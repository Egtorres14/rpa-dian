# Comparación de modalidades

Todas las cifras son tiempos de pared medidos; no se extrapolan a otros equipos.

| Modalidad | Alcance | Tiempo (s) | Tareas 2Captcha | Coste (USD) | Resultado |
|---|---|---:|---:|---:|---|
| Pago: lote integral | Consulta, descarga y OCR de diez facturas | 322.119 | 20 | 0.02900 | 9 OK, 1 REVISAR por código vacío |
| Pago: una factura | Consulta, descarga y OCR de una factura | 28.627 | 2 | 0.00290 | OK |
| Gratuito asistido | Portal con intervención si hace falta | Variable / no medido | 0 | 0 | Tiempo humano no medido en lote real |
| Widget: Chromium | Un intento sin proveedor ni intervención | 17.361 | 0 | 0 | No recibió token en 15 s; 0 PDF |
| Widget: Chrome | Un intento sin proveedor ni intervención | 17.255 | 0 | 0 | No recibió token en 15 s; 0 PDF |
| Demo OCR: diez PDF | Sólo punto 2, archivos incluidos, sin Internet | 65.128 | 0 | 0 | 9 OK, 1 REVISAR por código vacío |

La demo generó los diez CSV con exactamente los mismos datos de la referencia: 21 productos, 20 páginas, 113/114 campos presentes y 100 % de concordancia en 113 campos comparables. El código vacío de HTFE-6599 se conserva.

## Funcionamiento de 2Captcha en la prueba de una factura

La comprobación getBalance confirmó una clave válida y saldo positivo sin crear tareas. No se guarda la clave ni el importe del saldo en la comparación.

| CAPTCHA | Tiempo declarado por proveedor (s) | Tiempo del cliente (s) | Consultas de resultado | Coste (USD) |
|---|---:|---:|---:|---:|
| Búsqueda | 7 | 10.797 | 2 | 0.00145 |
| Descarga | 4 | 5.453 | 1 | 0.00145 |

El proveedor declaró 7 y 4 s para las soluciones; el cliente esperó 10,797 y 5,453 s. La diferencia incluye el sondeo, la red y la granularidad de los timestamps. Se conserva el intervalo mínimo de cinco segundos entre consultas pendientes. Una futura ejecución de servidor podría usar callback para recibir el resultado; no se midió todavía ese cambio.

## Qué se puede demostrar gratis

La demo del OCR es automática y funciona con los archivos incluidos después de instalar las dependencias. La opción gratis del portal permite intervención humana cuando el widget no produce un token. Las pruebas controladas verifican ambos tokens, descarga POST con CSRF y que nunca se utiliza el proveedor, incluso si hay clave configurada. No se registró un lote real asistido completo ni se le asignó un tiempo estimado.

## Uso del saldo sin divulgar la clave

El evaluador puede introducir su propia clave con --pedir-clave. Para usar el saldo del propietario sin recibir la API key, la ejecución de pago debe residir en un servidor controlado o GitHub Actions con secretos y código revisado. El repositorio descargable no contiene la clave. La publicación del repositorio no configura por sí sola esa ejecución remota.

Comandos y referencias oficiales: [Claves y modalidades](CLAVES_Y_MODALIDADES.md).
