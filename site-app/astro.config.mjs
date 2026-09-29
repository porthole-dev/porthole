import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import sidebar from './sidebar.generated.mjs';

const [owner, repository] = (process.env.GITHUB_REPOSITORY ?? 'porthole-dev/porthole').split('/');

export default defineConfig({
  site: `https://${owner}.github.io`,
  base: repository === `${owner}.github.io` ? '/' : `/${repository}`,
  outDir: './dist',
  integrations: [starlight({
    title: 'Porthole · Device bring-up',
    description: 'Independent Pixel 2 XL port, build guidance and release evidence.',
    logo: { src: './src/assets/porthole.svg', alt: 'Porthole compass mark' },
    favicon: '/porthole.svg',
    social: [{ icon: 'github', label: 'GitHub', href: `https://github.com/${owner}/${repository}` }],
    sidebar,
    customCss: ['./src/styles/editorial.css'],
    pagefind: true,
    expressiveCode: { themes: ['github-light', 'github-dark'] },
  })],
});
