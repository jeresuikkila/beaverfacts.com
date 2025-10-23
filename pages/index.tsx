import type { NextPage, GetStaticProps } from "next";
import Head from "next/head";
import Image from "next/image";
import styles from "../styles/Home.module.css";

import facts from "../public/beaverfacts.json";

interface HomeProps {
  fact: string;
  source: string;
}

const Home: NextPage<HomeProps> = ({ fact, source }) => {
  const siteUrl = "https://beaverfacts.com";
  const description = "Discover fascinating facts about beavers! Learn about these amazing rodents, their dams, lodges, and important role in ecosystems.";

  return (
    <div className={styles.container}>
      <Head>
        <title>Beaverfacts.com - Amazing Facts About Beavers</title>
        <meta name="description" content={description} />
        <link rel="icon" href="/favicon.png" />

        {/* Open Graph / Facebook */}
        <meta property="og:type" content="website" />
        <meta property="og:url" content={siteUrl} />
        <meta property="og:title" content="Beaverfacts.com - Amazing Facts About Beavers" />
        <meta property="og:description" content={description} />
        <meta property="og:image" content={`${siteUrl}/Beaverhugs1.png`} />

        {/* Twitter */}
        <meta property="twitter:card" content="summary_large_image" />
        <meta property="twitter:url" content={siteUrl} />
        <meta property="twitter:title" content="Beaverfacts.com - Amazing Facts About Beavers" />
        <meta property="twitter:description" content={description} />
        <meta property="twitter:image" content={`${siteUrl}/Beaverhugs1.png`} />

        {/* Additional SEO */}
        <meta name="keywords" content="beavers, beaver facts, wildlife, rodents, dams, lodges, nature, animals" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </Head>

      <main className={styles.main}>
        <Image
          src="/Beaverhugs1.png"
          alt="Drawing of a beaver hugging a maple leaf"
          width={500}
          height={500}
          priority
          placeholder="blur"
          blurDataURL="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mN8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
        />
        <p>{fact}</p>
      </main>
    </div>
  );
};

export const getStaticProps: GetStaticProps<HomeProps> = async () => {
  // Select a random fact at build time
  const randomFact = facts.facts[Math.floor(Math.random() * facts.facts.length)];

  return {
    props: {
      fact: randomFact.fact,
      source: randomFact.source,
    },
    // Revalidate every 3600 seconds (1 hour) to show different facts
    revalidate: 3600,
  };
};

export default Home;
