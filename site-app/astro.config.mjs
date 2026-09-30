import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import sidebar from './sidebar.generated.mjs';

const [owner, repository] = (process.env.GITHUB_REPOSITORY ?? 'porthole-dev/porthole').split('/');

export default defineConfig({
  site: `https://${owner}.github.io`,
  base: repository === `${owner}.github.io` ? '/' : `/${repository}`,
  outDir: './dist',
  integrations: [starlight({
    title: 'Porthole',
    description: 'Nura on community-supported phones. Device support, downloads, and development guides.',
    logo: { src: './public/organization.png', alt: 'Porthole organization icon' },
    favicon: '/organization.png',
    social: [{ icon: 'github', label: 'GitHub', href: `https://github.com/${owner}/${repository}` }],
    sidebar,
    components: { Head: './src/components/CatalogHead.astro' },
    customCss: ['./src/styles/editorial.css'],
    pagefind: true,
    expressiveCode: { themes: ['github-light', 'github-dark'] },
  })],
});
