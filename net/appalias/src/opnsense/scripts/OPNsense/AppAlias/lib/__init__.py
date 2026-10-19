# Copyright (C) 2026 spaghiari
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
# INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
# OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""Bibliothèque interne du démon appaliasd (os-appalias).

Modules :
- dnswire : lecture bornée des réponses DNS (question, A, AAAA, CNAME) ;
- matcher : correspondance des noms avec les règles des applications ;
- pftable : appels à pfctl sans shell ;
- state   : IP résolues, expirations, sauvegarde atomique ;
- config  : lecture de config.json produit par export_config.php ;
- shared  : garde-fou des plateformes partagées (data/shared_platforms.txt) ;
- v2fly   : analyseur du catalogue v2fly embarqué (catalog/v2fly.lst) ;
- unboundfwd : zones de transfert Unbound vers le relais (dédoublonnage).
- v2fly_snapshot : génération d'un instantané v2fly (outil et pare-feu) ;
- version : version du code (PLUGIN_VERSION).

Stdlib uniquement.
"""
