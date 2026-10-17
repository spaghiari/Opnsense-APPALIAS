{% if helpers.exists('OPNsense.appalias.general.enabled') and OPNsense.appalias.general.enabled == '1' %}
appaliasd_enable="YES"
{% else %}
appaliasd_enable="NO"
{% endif %}
