m

# Installed AI Agent Skills & MCPs

## Globally Installed Skills

1. **bklit-ui**

   - **Purpose**: Bklit UI charts and data visualization for any project using the `@bklit` shadcn registry.
   - **Features**: Installs the bklit-ui skill to give agents project-aware context about Bklit UI. Understands how to install, compose, theme, and animate charts using the correct APIs and patterns (such as `chartCssVars`, `ChartTooltip`, and `useChart`).
   - **Path**: `~/.agents/skills/bklit-ui` and `~/.gemini/config/skills/bklit-ui`
2. **kokonut-ui** (from `kokonut-labs/kokonutui`)

   - **Purpose**: Kokonut UI React components using Tailwind CSS v4 and Motion.
   - **Features**: Understands how to set up namespaces with `components.json` and install Kokonut UI components (e.g., `particle-button`) via the shadcn CLI or direct registry URLs. Includes related design skills like `frontend-design`, `next-best-practices`, etc.
   - **Path**: `~/.agents/skills/building-components`
3. **react-spring** (Custom Skill)

   - **Purpose**: Guidelines and documentation for using react-spring to build interactive, data-driven, and animated UI components.
   - **Features**: Understands the `animated` HOC, the `useSpring` hook, SpringValues, Controllers, and the imperative `api.start()` method for building animations without React re-renders.
   - **Path**: `~/.gemini/config/skills/react-spring`
4. **Motion AI Kit** (`motion-ai`)

   - **Purpose**: Provides context for Motion (formerly Framer Motion), including best practices, documentation search, and CSS spring generation.
   - **Features**: Integrates an MCP server with current Motion docs, free examples search, and animation best practices.
   - **Path**: Installed via MCP / `motion-ai`
5. **Watermelon UI** (`watermelon-ui`)

   - **Purpose**: High-quality React components registry built on shadcn and Tailwind CSS v4.
   - **Features**: Understands how to install and use Watermelon UI components using the `npx shadcn@latest add https://registry.watermelon.sh/r/...` pattern.
   - **Path**: `~/.gemini/config/skills/watermelon-ui`

## Installed MCP Servers

- **StitchMCP**: For managing UI design systems, screens, generating screens from text, and applying design variants.
- **context7**: For fetching up-to-date documentation on libraries, frameworks, SDKs, and APIs (`resolve-library-id`, `query-docs`).
- **refero**: For searching screens, flows, styles, and UI inspiration.
- **serena**: System integration, project manipulation, codebase memory, and shell execution capabilities.

## Design & Engineering Skills (Recent.Design Bundle)

A large suite of design, UX, and development skills installed globally:

- **Anthropics (canvas-design, frontend-design)**: Generate visual art and production-ready frontend interfaces.
- **Pbakaus (impeccable suite)**: Includes `quieter`, `distill`, `critique`, and `polish` to refine and perfect UI/UX.
- **Vercel Labs (agent-skills, agent-browser)**: Web design guidelines, React/Next.js best practices, composition patterns, and persistent browser automation.
- **Emil Kowalski**: `prototype`, `apple-design`, `animation-vocabulary`, `emil-design-eng`, `review-animations` for high-craft interfaces, fluid motion, and visual prototyping.
- **Matt Pocock (`grill-me`)**: Rigorous design grilling to expose gaps in architecture and UI decisions.
- **Others**: UI defaults (`ibelick/ui-skills`), intentional details (`jakubkrehel/make-interfaces-feel-better`), web color systems (`oklch-skill`), UI design tokens (`extract-design-system`), advanced TypeScript (`wshobson/typescript-advanced-types`), and `shadcn` management.
