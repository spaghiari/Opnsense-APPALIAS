"""Bibliothèque interne du démon appaliasd (os-appalias).

Modules :
- dnswire : lecture bornée des réponses DNS (question, A, AAAA, CNAME) ;
- matcher : correspondance des noms avec les règles des applications ;
- pftable : appels à pfctl sans shell ;
- state   : IP résolues, expirations, sauvegarde atomique ;
- config  : lecture de config.json produit par export_config.php.

Stdlib uniquement.
"""
