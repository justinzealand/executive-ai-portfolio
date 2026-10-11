# Two-site Cloudflare activation plan

## Approved architecture

- Keep Zealand Technology and the executive portfolio as separate sites.
- Host both as separate Cloudflare Pages projects in the same Cloudflare account.
- Use `https://zealand.technology/` for the corporate site.
- Use `https://portfolio.zealand.technology/` for the executive portfolio.
- Preserve the private Tailnet previews on ports 8765 and 8447 for pre-public validation.

## Prepared in source

- Portfolio canonical, Open Graph, sitemap, and robots URLs use `portfolio.zealand.technology`.
- The portfolio company mark and footer link to `zealand.technology`.
- The corporate public build links to `portfolio.zealand.technology` while its private preview continues to use Tailnet port 8447.
- GitHub Pages deployment has been removed from the portfolio workflow; CI continues to build and verify the generated site.

## Activation gate

Public activation remains a separate operator-reviewed external action. Before activation, the agent must:

1. Reconfirm the exact public repository visibility and source branch for each site.
2. Inventory the live Cloudflare zones and DNS records, preserving MX, SPF, DKIM, DMARC, and verification records.
3. Create or connect the two separate Cloudflare Pages projects with their existing repository build commands and `dist` output directories.
4. Attach only the approved custom hostname to each project.
5. Make the evidence repository publicly readable only after its public-safety gate, generated-site checks, and repository presentation review pass.
6. Verify HTTPS, canonical URLs, robots and sitemap files, desktop and mobile rendering, two-way navigation, résumé download, and contact links on the public hostnames.
7. Confirm the private Tailnet previews remain reachable and that no Tailscale Funnel route was created.

## Rollback

- Remove or disable only the affected Cloudflare custom hostname or Pages deployment.
- Restore the prior DNS record from the pre-change inventory.
- Leave the source repositories and private Tailnet previews intact.
