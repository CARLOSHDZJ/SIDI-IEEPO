# SIDI - Sistema de Despacho de Inventarios Escolares (IEEPO)
## Estado Actual del Proyecto y Operación en Producción

**Fecha de última sincronización:** 25 de Septiembre de 2026  
**Repositorio GitHub:** https://github.com/CARLOSHDZJ/SIDI-IEEPO.git  
**Entorno de Servidor:** Proxmox VE (Nodo ofi, Contenedor LXC 101: sidi-despacho, Ubuntu 24.04 LTS)  
**Estatus:** 🚀 **EN PRODUCCIÓN (FASE PILOTO ACTIVA Y 100% OPERATIVA)**

---

### 1. Direcciones y Puntos de Acceso

* **Ventanilla Digital Pública Oficial (Acceso Global):**  
  👉 **https://sidi-demo.ejemplo.gob.mx**  
  *(Conexión cifrada HTTPS/SSL activa vía Tailscale Funnel. Accesible desde cualquier celular, red móvil o computadora sin necesidad de instalar nada).*

* **Panel Administrativo y Analítica:**  
  👉 **https://sidi-demo.ejemplo.gob.mx/admin**  
  *(Monitoreo en vivo de folios, métricas, reintentos y exportación a Excel).*

* **Acceso Interno / Red Tailscale:**  
  * IP Remota Tailscale: 100.64.0.10:8000
  * SSH Directo: ssh admin@servidor-demo
  * Red LAN Oficina IEEPO: http://10.0.0.10:8000

---

### 2. Ecosistema de Automatización Operativo

1. **Ventanilla Web y Verificación OTP:**
   * Solicitud en línea con Clave de Centro de Trabajo (CCT), nombre, cargo, teléfono y correo electrónico.
   * Envío automático de código de seguridad de 6 dígitos para validar la legitimidad del correo del solicitante.
   * Opción administrativa para omitir/aprobar sin OTP cuando se requiera.

2. **Robot de Extracción Playwright (Headless en Linux):**
   * Worker asíncrono secuencial (FIFO) que inicia sesión en el portal central ExtJS del IEEPO.
   * Localiza el Centro de Trabajo, extrae la Cédula Censal oficial en formato PDF y la almacena de forma segura.

3. **Mailing y Despacho Automatizado:**
   * Conexión SMTP saliente por Google Mail (smtp.gmail.com:587 STARTTLS) mediante App Password.
   * Envío del correo oficial con remitente institucional (inventarios.atencion@ejemplo.gob.mx).
   * Adjuntos normativos 2026 automáticos:
     * Cédula Censal oficial del CCT extraída.
     * ALTA_20260912010045.xlsx (Formato Oficial de Alta de Bienes Muebles).
     * BAJAS_20260912010450.xlsx (Formato Oficial de Baja de Mobiliario).
     * BAJA_20260912010617.pdf (Normativa y requisitos de baja).
     * INSTRUCTIVO DE ALTAS_20260912010259.pdf (Guía de cotejo físico).
     * TRASFERENCIA_20260912010726.xlsx (Formato de traslado entre planteles).

4. **Canalización por Respuesta Automática en Gmail:**
   * Configurada en inventarios.atencion@ejemplo.gob.mx para canalizar a los solicitantes a la ventanilla digital inmediatamente.

---

### 3. Blindaje de Red y DNS (25/Sep/2026)

* **Resolución DNS Redundante:** Configurados servidores Anycast mundiales de Google (8.8.8.8) y Cloudflare (1.1.1.1) directamente en la configuración de Proxmox para el contenedor 101. Esto garantiza que el servicio no dependa del router/módem local de la oficina y evita fallas de 'name resolution'.
* **Zonas Horarias Estandarizadas:** Módulo de tiempo configurado formalmente en America/Mexico_City para todos los registros, OTP y auditorías.

---

### 4. Estado del Servicio en el Servidor Linux

* **Servicio Systemd:** sidi.service (activo, habilitado para auto-arranque continuo 24/7 con reinicio automático ante cortes de luz o reinicios programados).
* **Ruta de instalación:** /opt/sidi
* **Entorno virtual:** /opt/sidi/venv
* **Túnel Tailscale Funnel:** En segundo plano en puerto 443 hacia 127.0.0.1:8000.
* **Comandos de control en consola:**
  * Estado del servicio: systemctl status sidi
  * Reiniciar servicio: systemctl restart sidi
  * Ver logs en tiempo real: journalctl -u sidi -f
  * Estado de Funnel: tailscale funnel status
